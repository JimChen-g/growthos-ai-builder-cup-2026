// Public Firebase browser identifiers, frozen in the same build as the client JS.
// Never add provider credentials or server-only settings here.
export const dynamic = "force-static";

export function GET() {
  return Response.json({
    schema: 1,
    firebase: {
      apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
      authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
      projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
      appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
    },
  });
}
