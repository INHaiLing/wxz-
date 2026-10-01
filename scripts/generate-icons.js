// 将仓库自有线性图形渲染为微信小程序使用的本地 PNG。
const fs = require('node:fs');
const path = require('node:path');
const sharp = require('sharp');
const destination = path.join(__dirname, '../miniprogram/assets/icons');
const drawings = {
  book: '<path d="M32 14c-7-5-15-5-23-2v38c8-3 16-3 23 2 7-5 15-5 23-2V12c-8-3-16-3-23 2z"/><path d="M32 14v38"/>',
  dice: '<rect x="9" y="9" width="46" height="46" rx="9"/><g fill="#C47A2B" stroke="none"><circle cx="22" cy="22" r="4"/><circle cx="42" cy="22" r="4"/><circle cx="32" cy="32" r="4"/><circle cx="22" cy="42" r="4"/><circle cx="42" cy="42" r="4"/></g>',
  classical: '<path d="M13 45L43 15c2-2 5-2 7 0l2 2c2 2 2 5 0 7L22 54l-13 3z"/><path d="M38 20l11 11M13 45l9 9M46 14l6 6"/>',
  translate: '<path d="M15 7h24l12 12v37H15z"/><path d="M39 7v13h12M24 29h18M24 38h18M24 47h12"/>',
  favorite: '<path d="M18 8h28v48L32 47 18 56z" fill="#C47A2B" stroke="#C47A2B"/>',
  'arrow-right': '<path d="M24 13l19 19-19 19"/>'
};
async function main() {
  fs.mkdirSync(destination, { recursive: true });
  const variants = { green: '#1B4D3E', amber: '#C47A2B', white: '#FFFFFF', muted: '#5C6460' };
  for (const [name, drawing] of Object.entries(drawings)) {
    for (const [variant, color] of Object.entries(variants)) {
      const tinted = drawing.replaceAll('#C47A2B', color);
      const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 64 64"><g fill="none" stroke="' + color + '" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round">' + tinted + '</g></svg>';
      await sharp(Buffer.from(svg)).png().toFile(path.join(destination, name + '-' + variant + '.png'));
    }
    const defaultVariant = name === 'dice' || name === 'favorite' ? 'amber' : 'green';
    fs.copyFileSync(path.join(destination, name + '-' + defaultVariant + '.png'), path.join(destination, name + '.png'));
  }
  const aliases = { translation: 'translate', collection: 'favorite', arrow: 'arrow-right', 'dice-3d': 'dice' };
  for (const [alias, name] of Object.entries(aliases)) {
    for (const suffix of ['', ...Object.keys(variants).map((name) => '-' + name)]) fs.copyFileSync(path.join(destination, name + suffix + '.png'), path.join(destination, alias + suffix + '.png'));
  }
  console.log('Generated 10 Qingmo icons with four semantic color variants.');
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
