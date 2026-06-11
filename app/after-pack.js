// electron-builder afterPack hook: embed the app icon + version metadata
// into the packed executable ourselves. electron-builder's own pass
// (signAndEditExecutable) needs its winCodeSign cache, whose darwin
// symlinks fail to extract without Windows Developer Mode - rcedit from
// npm has no such baggage.

const path = require("path");
const rceditMod = require("rcedit");
const rcedit = rceditMod.default || rceditMod.rcedit || rceditMod;
const pkg = require("./package.json");

module.exports = async function afterPack(context) {
  if (context.electronPlatformName !== "win32") return;
  const exe = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.exe`);
  await rcedit(exe, {
    icon: path.join(__dirname, "build", "icon.ico"),
    "version-string": {
      ProductName: pkg.productName,
      FileDescription: pkg.description,
      CompanyName: pkg.author,
      LegalCopyright: `MIT - ${pkg.author}`,
    },
    "file-version": pkg.version,
    "product-version": pkg.version,
  });
  console.log(`  • after-pack: icon + version metadata embedded into ${path.basename(exe)}`);
};
