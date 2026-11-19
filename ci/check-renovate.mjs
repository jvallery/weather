// SPDX-License-Identifier: MIT
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [dist, input, trace] = process.argv.slice(2);
const load = relative => import(pathToFileURL(join(dist, relative)).href);
const { GlobalConfig } = await load('config/global.js');
const { getPnpmLock } = await load('modules/manager/npm/extract/pnpm.js');
const { applyPackageRules } = await load('util/package-rules/index.js');
const { parseYaml } = await load('util/yaml.js');
const expected = {
  '@react-router/express': '8.5.0',
  '@react-router/node': '8.5.0',
  '@tanstack/react-query': '5.104.5',
  express: '5.2.1',
  'prom-client': '15.1.3',
  react: '19.3.1',
  'react-dom': '19.3.1',
  'react-router': '8.5.0',
  uplot: '1.6.32',
  zod: '4.6.7',
};
const packageFile = 'web/package.json';
const lockFile = 'web/pnpm-lock.yaml';
const config = JSON.parse(readFileSync(join(input, 'renovate.json'), 'utf8'));
const manifest = JSON.parse(readFileSync(join(input, packageFile), 'utf8'));
const logs = readFileSync(trace, 'utf8').trim().split('\n').map(line => JSON.parse(line));
assert.ok(logs.every(event => event.level < 50), 'Renovate emitted an error');
assert.equal(logs.filter(event => event.msg === 'Dependency extraction complete').length, 1);
const reports = logs.filter(event => event.msg === 'packageFiles');
assert.equal(reports.length, 1);
assert.deepEqual(Object.keys(reports[0].config), ['npm']);
const files = reports[0].config.npm;
assert.equal(files.length, 1);
const [pkg] = files;
assert.equal(pkg.packageFile, packageFile);
assert.equal(pkg.managerData.pnpmLockFile, lockFile);

const eligible = {};
const development = {};
for (const dep of pkg.deps) {
  const scored = await applyPackageRules({
    ...dep,
    manager: 'npm',
    packageFile,
    packageRules: config.packageRules,
  }, 'pre-lookup');
  if (dep.depType === 'dependencies') {
    assert.equal(dep.datasource, 'npm', dep.depName);
    assert.equal(dep.skipReason, undefined, dep.depName);
    assert.notEqual(scored.enabled, false, dep.depName);
    assert.equal(scored.skipReason, undefined, dep.depName);
    assert.ok(!Object.hasOwn(eligible, dep.depName), dep.depName);
    eligible[dep.depName] = dep.currentValue;
  } else {
    assert.ok(['devDependencies', 'engines', 'packageManager'].includes(dep.depType), dep.depName);
    assert.equal(scored.enabled, false, dep.depName);
    assert.equal(scored.skipReason, 'package-rules', dep.depName);
    if (dep.depType === 'devDependencies') {
      assert.ok(!Object.hasOwn(development, dep.depName), dep.depName);
      development[dep.depName] = dep.currentValue;
    }
  }
}
assert.deepEqual(eligible, expected);
assert.deepEqual(development, manifest.devDependencies);

const lockDocs = parseYaml(readFileSync(join(input, lockFile), 'utf8'));
assert.equal(lockDocs.length, 2);
assert.equal(lockDocs[0].importers['.'].packageManagerDependencies.pnpm.version, '12.15.0');
const production = lockDocs[1].importers['.'].dependencies;
assert.deepEqual(Object.fromEntries(Object.entries(production).map(([name, dep]) => [name, dep.specifier])), expected);
GlobalConfig.set({ localDir: input, platform: 'local' });
const lock = await getPnpmLock(lockFile);
assert.equal(lock.lockfileVersion, 9);
assert.deepEqual(lock.lockedVersionsWithPath['.'].dependencies, expected);
console.log('Renovate extracted 10 exact production pins from web/package.json and resolved all from the second pnpm lock document. Owner package rules exclude the other extracted dependency types.');
