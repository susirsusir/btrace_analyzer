#!/bin/bash

cd "$(dirname "$0")/perfetto-trace-analyzer-extension"

# 自动增加版本号
MANIFEST="manifest.json"
PACKAGE="package.json"
CURRENT_VERSION=$(grep -o '"version": "[^"]*"' $MANIFEST | cut -d'"' -f4)
IFS='.' read -ra PARTS <<< "$CURRENT_VERSION"
PARTS[2]=$((PARTS[2] + 1))
NEW_VERSION="${PARTS[0]}.${PARTS[1]}.${PARTS[2]}"

echo "版本号: $CURRENT_VERSION -> $NEW_VERSION"
sed -i '' "s/\"version\": \"$CURRENT_VERSION\"/\"version\": \"$NEW_VERSION\"/" $MANIFEST
sed -i '' "s/\"version\": \"[^\"]*\"/\"version\": \"$NEW_VERSION\"/" $PACKAGE

echo "清理旧的打包文件..."
rm -rf dist-zip
mkdir -p dist-zip

echo "打包插件..."
# 仅打包扩展需要的文件，排除源码测试、node_modules、脚本本身等
zip -r dist-zip/perfetto-trace-analyzer-extension-v${NEW_VERSION}.zip . \
  -x "*.DS_Store" \
  -x "node_modules/*" \
  -x "*.test.js" \
  -x "build-clean.sh" \
  -x "package-lock.json" \
  -x "babel.config.js" \
  -x "dist-zip/*"

echo "完成！插件版本 $NEW_VERSION 已保存到 dist-zip/perfetto-trace-analyzer-extension-v${NEW_VERSION}.zip"
