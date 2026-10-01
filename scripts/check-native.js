const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const root = path.resolve(__dirname, '..');
const miniRoot = path.join(root, 'miniprogram');
const compilerDir = process.env.WECHAT_COMPILER_DIR ||
  'C:/Program Files (x86)/Tencent/微信web开发者工具/code/package.nw/node_modules/wcc-exec';
const cacheDir = path.join(root, '.devtools', 'native-compile');

function decode(buffer) {
  if (!buffer || !buffer.length) return '';
  try { return new TextDecoder('utf-8', { fatal: true }).decode(buffer); }
  catch (_) { return new TextDecoder('gb18030').decode(buffer); }
}

function sourceFiles(extension) {
  const files = [];
  function visit(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const filename = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(filename);
      else if (entry.name.endsWith(extension)) {
        files.push(`./${path.relative(miniRoot, filename).split(path.sep).join('/')}`);
      }
    }
  }
  visit(miniRoot);
  return files.sort();
}

function invoke(executable, args) {
  const result = spawnSync(executable, args, {
    cwd: miniRoot,
    windowsHide: true,
    timeout: 30000,
    maxBuffer: 32 * 1024 * 1024
  });
  return {
    exitCode: result.status,
    signal: result.signal || null,
    error: result.error ? result.error.message : null,
    output: [decode(result.stdout), decode(result.stderr)].filter(Boolean).join('\n')
  };
}

function compile(name, executable, files, compilerArgs) {
  const outputFile = path.join(cacheDir, name === 'wxml' ? 'wxml.generated.js' : 'wxss.compiled.txt');
  // Remove this one cached output so a stale artifact cannot count as success.
  if (fs.existsSync(outputFile)) fs.unlinkSync(outputFile);
  const outputArg = path.relative(miniRoot, outputFile).split(path.sep).join('/');
  const args = compilerArgs.concat(['-o', outputArg], files);
  const run = invoke(executable, args);
  fs.writeFileSync(path.join(cacheDir, `${name}.log`), run.output);
  const outputBytes = fs.existsSync(outputFile) ? fs.statSync(outputFile).size : 0;
  const compiled = outputBytes ? fs.readFileSync(outputFile, 'utf8') : '';
  let missingFiles;
  if (name === 'wxss') {
    // wcsc -js returns escaped code records separated by "=", not one JS file.
    // Validate the emitted stylesheet keys to confirm all roots were compiled.
    const parts = compiled.split('=');
    const keys = new Set(parts.filter((_, index) => index % 2 === 0));
    missingFiles = files.filter((file) => !keys.has(file));
  } else missingFiles = files.filter((file) => !compiled.includes(file));
  const componentStyleWarnings = name === 'wxss'
    ? Array.from(new Set(Array.from(
      compiled.matchAll(/Some selectors are not allowed[^=]*?\(([^)]+:\d+:\d+)\)/g),
      (match) => match[1]
    ))).filter((location) => location.startsWith('./components/'))
    : [];
  return {
    ...run,
    executable,
    args,
    files,
    outputFile,
    outputBytes,
    outputFormat: name === 'wxml' ? 'javascript' : 'wechat-escaped-style-records',
    missingFiles,
    componentStyleWarnings,
    success: !run.error && run.exitCode === 0 && outputBytes > 0 && missingFiles.length === 0
  };
}

function main() {
  const wcc = path.join(compilerDir, 'wcc.exe');
  const wcsc = path.join(compilerDir, 'wcsc.exe');
  for (const executable of [wcc, wcsc]) {
    if (!fs.existsSync(executable)) {
      throw new Error(`Native compiler not found: ${executable}. Set WECHAT_COMPILER_DIR to its directory.`);
    }
  }
  const wxmlFiles = sourceFiles('.wxml');
  const wxssFiles = sourceFiles('.wxss');
  if (!wxmlFiles.length || !wxssFiles.length) throw new Error('No WXML/WXSS source files found.');
  fs.mkdirSync(cacheDir, { recursive: true });

  const wccHelp = invoke(wcc, ['--help']);
  const wcscHelp = invoke(wcsc, ['--help']);
  fs.writeFileSync(path.join(cacheDir, 'wcc-help.log'), wccHelp.output);
  fs.writeFileSync(path.join(cacheDir, 'wcsc-help.log'), wcscHelp.output);
  const wccVersion = (wccHelp.output.match(/Wechat WXML Compiler[^\r\n]*/) || ['unknown'])[0];
  const wcscVersion = (wcscHelp.output.match(/WeChat Stylesheet Compiler[^\r\n]*/) || ['unknown'])[0];

  const wxml = compile('wxml', wcc, wxmlFiles, ['-d']);
  // -pc makes every input a root stylesheet, rather than treating later files
  // as imports of only the first stylesheet. -js emits escaped style records.
  const wxss = compile('wxss', wcsc, wxssFiles, ['-lc', '-js', '-pc', String(wxssFiles.length)]);
  const report = {
    checkedAt: new Date().toISOString(),
    sourceRoot: miniRoot,
    wccVersion,
    wcscVersion,
    wxml,
    wxss,
    success: wxml.success && wxss.success,
    runtimeVerified: false
  };
  const reportFile = path.join(cacheDir, 'result.json');
  fs.writeFileSync(reportFile, `${JSON.stringify(report, null, 2)}\n`);

  console.log(wccVersion);
  console.log(wcscVersion);
  for (const [kind, result] of [['WXML', wxml], ['WXSS', wxss]]) {
    console.log(`${result.success ? 'PASS' : 'FAIL'}: native ${kind} compilation, ${result.files.length} files, ${result.outputBytes} output bytes.`);
    if (!result.success) console.error(result.error || result.output ||
      (result.missingFiles.length ? `Missing compiled files: ${result.missingFiles.join(', ')}` : `Compiler exited with status ${result.exitCode}.`));
    else if (result.output.trim()) console.log(result.output.trim());
    for (const location of result.componentStyleWarnings) {
      console.warn(`WARN: native component selector restriction at ${location}.`);
    }
  }
  console.log(`Report: ${reportFile}`);
  console.log('This check compiles WXML/WXSS only; it does not verify simulator or device execution.');
  if (!report.success) process.exitCode = 1;
}

try { main(); }
catch (error) { console.error(error.message); process.exitCode = 1; }
