import { FirebaseApp, getApp, getApps, initializeApp } from "@firebase/app";
import { getAuth, signInAnonymously } from "@firebase/auth";

let signInPromise: Promise<string> | null = null;

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

export async function getFirebaseIdToken(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  const app = configuredApp();
  if (!app) return null;
  if (!signInPromise) {
    signInPromise = (async () => {
      const auth = getAuth(app);
      const user = auth.currentUser ?? (await signInAnonymously(auth)).user;
      return user.getIdToken();
    })();
  }
  try {
    return await signInPromise;
  } catch (error) {
    signInPromise = null;
    throw error;
  }
}
