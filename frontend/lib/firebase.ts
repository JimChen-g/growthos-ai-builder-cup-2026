import { type FirebaseApp, getApp, getApps, initializeApp } from "@firebase/app";
import {
  type User, GoogleAuthProvider, getAuth, onAuthStateChanged, signInAnonymously,
  signInWithPopup, signInWithRedirect, signOut,
} from "@firebase/auth";

let signInPromise: Promise<User> | null = null;
let identityEpoch = 0;
let identityChanging = false;
let interactiveAttempt = 0;
const POPUP_WAIT_MS = 45000;

function authErrorCode(error: unknown): string {
  if (!error || typeof error !== "object" || !("code" in error)) return "";
  return typeof error.code === "string" ? error.code : "";
}

function shouldUseRedirect(error: unknown): boolean {
  const code = authErrorCode(error);
  return code === "auth/popup-blocked"
    || code === "auth/operation-not-supported-in-this-environment"
    || code === "auth/web-storage-unsupported";
}

function configuredApp(): FirebaseApp | null {
  const config = {
    apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
    authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
    projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
    appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
  };
  if (!config.apiKey || !config.authDomain || !config.projectId || !config.appId) return null;
  return getApps().length ? getApp() : initializeApp(config);
}

export type FirebaseIdentity = { uid: string; email: string | null; anonymous: boolean };

export function observeFirebaseIdentity(onChange: (identity: FirebaseIdentity | null) => void): () => void {
  const app = configuredApp();
  if (!app) { onChange(null); return () => {}; }
  return onAuthStateChanged(getAuth(app), (user) => {
    identityEpoch += 1;
    onChange(user ? { uid: user.uid, email: user.email, anonymous: user.isAnonymous } : null);
  });
}

export function firebaseIdentityKey(): string | null {
  const app = configuredApp();
  const uid = app ? getAuth(app).currentUser?.uid : null;
  return identityChanging ? `changing:${identityEpoch}` : uid ? `${uid}:${identityEpoch}` : null;
}

export async function signInWithGoogle(): Promise<void> {
  const app = configuredApp();
  if (!app) throw new Error("auth/not-configured");
  // Do not defer opening the popup beyond the click gesture or race an earlier
  // anonymous sign-in. The UI can retry once that session has settled.
  if (signInPromise) throw new Error("auth/session-initializing");
  const attempt = ++interactiveAttempt;
  identityChanging = true;
  identityEpoch += 1;
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    const provider = new GoogleAuthProvider();
    provider.setCustomParameters({ prompt: "select_account" });
    // Invoke synchronously from the user gesture. Some embedded browsers never
    // resolve unsupported popups, so waiting must have a visible, bounded exit.
    const auth = getAuth(app);
    try {
      const popup = signInWithPopup(auth, provider);
      await Promise.race([
        popup,
        new Promise<never>((_resolve, reject) => {
          timeout = setTimeout(() => reject(Object.assign(new Error("auth/popup-timeout"), { code: "auth/popup-timeout" })), POPUP_WAIT_MS);
        }),
      ]);
    } catch (error) {
      if (!shouldUseRedirect(error)) throw error;
      // Redirect only when Firebase explicitly reports an unsupported popup.
      // A slow account selection or MFA flow must never navigate the page away.
      await signInWithRedirect(auth, provider);
    }
  } finally {
    if (timeout !== undefined) clearTimeout(timeout);
    if (attempt === interactiveAttempt) identityChanging = false;
  }
}

export async function signOutFirebase(): Promise<void> {
  const app = configuredApp();
  if (!app) return;
  const attempt = ++interactiveAttempt;
  identityChanging = true;
  identityEpoch += 1;
  try {
    await signInPromise?.catch(() => undefined);
    await signOut(getAuth(app));
  } finally {
    if (attempt === interactiveAttempt) identityChanging = false;
  }
}

export async function getFirebaseIdToken(forceRefresh = false, requireRegistered = false): Promise<string | null> {
  if (typeof window === "undefined") return null;
  if (identityChanging) throw new Error("auth/identity-changing");
  const app = configuredApp();
  if (!app) return null;
  const auth = getAuth(app);
  await auth.authStateReady();
  let user = auth.currentUser;
  if (requireRegistered && (!user || user.isAnonymous)) throw new Error("auth/registered-user-required");
  if (!user) {
    // Share concurrent sign-ins, but never keep a resolved ID token: tokens expire.
    signInPromise ??= signInAnonymously(auth).then((credential) => credential.user);
    try {
      user = await signInPromise;
    } finally {
      signInPromise = null;
    }
  }
  if (identityChanging || auth.currentUser?.uid !== user.uid) throw new Error("auth/identity-changing");
  const token = await user.getIdToken(forceRefresh);
  if (identityChanging || auth.currentUser?.uid !== user.uid) throw new Error("auth/identity-changing");
  return token;
}
