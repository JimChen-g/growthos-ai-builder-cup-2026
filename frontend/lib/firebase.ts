import { type FirebaseApp, getApp, getApps, initializeApp } from "@firebase/app";
import { type User, getAuth, signInAnonymously } from "@firebase/auth";

let signInPromise: Promise<User> | null = null;

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

export async function getFirebaseIdToken(forceRefresh = false): Promise<string | null> {
  if (typeof window === "undefined") return null;
  const app = configuredApp();
  if (!app) return null;
  const auth = getAuth(app);
  await auth.authStateReady();
  let user = auth.currentUser;
  if (!user) {
    // Share concurrent sign-ins, but never keep a resolved ID token: tokens expire.
    signInPromise ??= signInAnonymously(auth).then((credential) => credential.user);
    try {
      user = await signInPromise;
    } finally {
      signInPromise = null;
    }
  }
  return user.getIdToken(forceRefresh);
}
