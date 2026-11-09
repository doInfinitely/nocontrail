// electron-builder afterSign hook: notarize the signed .app, but only when
// Apple credentials are present in the environment. With no credentials it
// prints a notice and returns, so an unsigned local `npm run pack` / `dist`
// still succeeds. Credentials are read from the environment and never stored.
//
// To notarize, set one of:
//   a notarytool keychain profile (preferred — the password never touches the environment;
//   create it once with `xcrun notarytool store-credentials <name> --apple-id … --team-id …`):
//   APPLE_KEYCHAIN_PROFILE=<name>
// or an App Store Connect API key:
//   APPLE_API_KEY=/path/to/AuthKey_XXXX.p8  APPLE_API_KEY_ID=XXXX  APPLE_API_ISSUER=<issuer-uuid>
// or an Apple ID app-specific password:
//   APPLE_ID=you@example.com  APPLE_APP_SPECIFIC_PASSWORD=xxxx-xxxx-xxxx-xxxx  APPLE_TEAM_ID=XXXXXXXXXX
//
// Signing itself is handled by electron-builder from a Developer ID Application
// certificate in your login keychain (or CSC_LINK/CSC_KEY_PASSWORD).
const { notarize } = require("@electron/notarize");

exports.default = async function notarizing(context) {
  const { electronPlatformName, appOutDir } = context;
  if (electronPlatformName !== "darwin") return;

  const appName = context.packager.appInfo.productFilename;
  const appPath = `${appOutDir}/${appName}.app`;

  const env = process.env;
  const hasProfile = Boolean(env.APPLE_KEYCHAIN_PROFILE);
  const hasApiKey = env.APPLE_API_KEY && env.APPLE_API_KEY_ID && env.APPLE_API_ISSUER;
  const hasAppleId = env.APPLE_ID && env.APPLE_APP_SPECIFIC_PASSWORD && env.APPLE_TEAM_ID;
  if (!hasProfile && !hasApiKey && !hasAppleId) {
    console.log("notarize: no Apple credentials in env — skipping (build is signed but un-notarized).");
    return;
  }

  const opts = hasProfile
    ? { appPath, keychainProfile: env.APPLE_KEYCHAIN_PROFILE }
    : hasApiKey
      ? { appPath, appleApiKey: env.APPLE_API_KEY, appleApiKeyId: env.APPLE_API_KEY_ID, appleApiIssuer: env.APPLE_API_ISSUER }
      : { appPath, appleId: env.APPLE_ID, appleIdPassword: env.APPLE_APP_SPECIFIC_PASSWORD, teamId: env.APPLE_TEAM_ID };

  console.log(`notarize: submitting ${appName}.app to Apple (this can take a few minutes)…`);
  await notarize(opts);
  console.log("notarize: done; staple runs next in the build.");
};
