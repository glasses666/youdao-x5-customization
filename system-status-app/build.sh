#!/bin/sh
set -eu

APPID=8090902000000001
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
QJSC=${QJSC:-qjsc}
BUILD="$ROOT/build"
DIST="$ROOT/dist"

command -v "$QJSC" >/dev/null || { echo "missing QuickJS 2020-07-05 qjsc: $QJSC" >&2; exit 1; }
command -v magick >/dev/null || { echo "missing ImageMagick" >&2; exit 1; }
command -v zip >/dev/null || { echo "missing zip" >&2; exit 1; }
command -v unzip >/dev/null || { echo "missing unzip" >&2; exit 1; }

mkdir -p "$BUILD" "$DIST"

compile_module() {
  name=$1
  if test "$name" = index; then
    (cd "$ROOT/src" && "$QJSC" -m -c -M systemInfo -M batteryInfo -N "qjsc_$name" -o "$BUILD/$name.c" "$name.js")
  else
    (cd "$ROOT/src" && "$QJSC" -m -c -N "qjsc_$name" -o "$BUILD/$name.c" "$name.js")
  fi
  awk '/const uint8_t .+\[[0-9]+\] = \{/{copy=1; next} copy && /};/{exit} copy{print}' "$BUILD/$name.c" \
    | grep -o '0x[0-9a-f][0-9a-f]' | sed 's/0x//' | tr -d '\n' | xxd -r -p > "$BUILD/$name.js.bin"
  test -s "$BUILD/$name.js.bin"
}

compile_module app
compile_module index
magick -background none "$ROOT/app_icon.svg" -resize 120x120 "$BUILD/app_icon.png"

file_cert() {
  file=$1
  size=$(wc -c < "$BUILD/$file" | tr -d ' ')
  digest=$(md5 -q "$BUILD/$file")
  printf '    "%s": {"size": %s, "md5": "%s"}' "$file" "$size" "$digest"
}

{
  printf '{\n'
  printf '  "appName": "系统状态",\n'
  printf '  "version": "1.0.1",\n'
  printf '  "appid": "%s",\n' "$APPID"
  printf '  "icon": "app_icon.png",\n'
  printf '  "quickjs": {"version": "20200705", "bigNum": false},\n'
  printf '  "meta": {"otaVersion": "3.4.0"},\n'
  printf '  "props": {"supportUnInstall": true, "addDesktop": {"coco_platform": true, "almond_platform": false, "x3s_platform": false, "apollo_platform": false, "plum_platform": false, "melon_platform": false}},\n'
  printf '  "cert": {\n'
  file_cert app.js.bin; printf ',\n'
  file_cert index.js.bin; printf ',\n'
  file_cert app_icon.png; printf '\n'
  printf '  }\n}\n'
} > "$BUILD/manifest.json"

(cd "$BUILD" && zip -X -q "$DIST/system-status.amr" manifest.json app.js.bin index.js.bin app_icon.png)
unzip -tqq "$DIST/system-status.amr"
echo "$DIST/system-status.amr"
