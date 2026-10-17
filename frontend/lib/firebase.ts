import { type FirebaseApp, getApp, getApps, initializeApp } from "@firebase/app";
import {
  type User, GoogleAuthProvider, getAuth, onAuthStateChanged, signInAnonymously,
  signInWithPopup, signOut,
} from "@firebase/auth";

let signInPromise: Promise<User> | null = null;
let identityEpoch = 0;
let identityChanging = false;

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
  identityChanging = true;
  identityEpoch += 1;
  try {
    // Do not let an earlier anonymous request overwrite the chosen account.
    await signInPromise?.catch(() => undefined);
    const provider = new GoogleAuthProvider();
    provider.setCustomParameters({ prompt: "select_account" });
    await signInWithPopup(getAuth(app), provider);
  } finally {
    identityChanging = false;
  }
}

export async function signOutFirebase(): Promise<void> {
  const app = configuredApp();
  if (!app) return;
  identityChanging = true;
  identityEpoch += 1;
  try {
    await signInPromise?.catch(() => undefined);
    await signOut(getAuth(app));
  } finally {
    identityChanging = false;
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
