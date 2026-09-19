const { build } = require('./package.json');
module.exports = {
  ...build,
  forceCodeSigning: true,
  mac: {
    ...build.mac,
    target: ['dmg', 'zip'],
    hardenedRuntime: true,
    notarize: true,
    entitlements: 'entitlements.mac.plist',
    entitlementsInherit: 'entitlements.mac.plist',
  },
};
