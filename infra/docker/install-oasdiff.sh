set -eu
version=1.33.0
destination=${1:-/usr/local/bin/oasdiff}
case "$(uname -s):$(uname -m)" in
  Linux:x86_64) archive=oasdiff_1.33.0_linux_amd64.tar.gz; checksum=43a4e328e2d13ba1552d760aa68d2485c75c5621f309f6ff64ae895188345247 ;;
  Linux:aarch64|Linux:arm64) archive=oasdiff_1.33.0_linux_arm64.tar.gz; checksum=4ae3c362d6074d919aada2dea82d0ee84366591600384455d0bdc658ddf8f7ae ;;
  Darwin:arm64|Darwin:x86_64) archive=oasdiff_1.33.0_darwin_all.tar.gz; checksum=2a479337c15afdcbf0b1e89c0b4d0cf0176472dbc11483358fed1f831d1e46c5 ;;
  *) echo 'Unsupported oasdiff installation platform' >&2; exit 1 ;;
esac
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT HUP INT TERM
curl --fail --location --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 2 "https://github.com/oasdiff/oasdiff/releases/download/v$version/$archive" -o "$temporary/$archive"
if command -v sha256sum >/dev/null 2>&1; then
  actual=$(sha256sum "$temporary/$archive" | cut -d ' ' -f 1)
else
  actual=$(shasum -a 256 "$temporary/$archive" | cut -d ' ' -f 1)
fi
test "$actual" = "$checksum" || { echo 'oasdiff checksum mismatch' >&2; exit 1; }
tar -xzf "$temporary/$archive" -C "$temporary" oasdiff
mkdir -p "$(dirname "$destination")"
install -m 755 "$temporary/oasdiff" "$destination"
test "$("$destination" --version)" = "oasdiff version $version"
