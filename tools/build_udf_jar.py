"""Pack compiled UDF classes into a byte-stable jar: python tools/build_udf_jar.py <classes> <jar>."""
import os
import sys
import zipfile

STAMP = (1980, 1, 1, 0, 0, 0)


def main(classes: str, target: str) -> None:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    files = sorted(os.path.relpath(os.path.join(root, name), classes).replace(os.sep, "/")
                   for root, _, names in os.walk(classes) for name in names)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as jar:
        manifest = zipfile.ZipInfo("META-INF/MANIFEST.MF", STAMP)
        jar.writestr(manifest, "Manifest-Version: 1.0\r\n\r\n", zipfile.ZIP_DEFLATED)
        for name in files:
            info = zipfile.ZipInfo(name, STAMP)
            info.external_attr = 0o644 << 16
            with open(os.path.join(classes, name), "rb") as fh:
                jar.writestr(info, fh.read(), zipfile.ZIP_DEFLATED)
    print(f"[udf] {len(files)} classes -> {target}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
