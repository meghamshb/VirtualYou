import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';

export function customerOrigin(value) {
  let url;
  try { url = new URL(value); } catch { throw Error('Set the public HTTPS baseUrl in desktop/service.json before packaging customers.'); }
  if (url.protocol !== 'https:' || url.username || url.password || url.pathname !== '/' || url.search || url.hash || !url.hostname.includes('.') || /(^|\.)(localhost|local|test|example|invalid)$/.test(url.hostname) || /^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(url.hostname))
    throw Error('Customer releases require a public HTTPS origin, without a path or credentials.');
  return url.origin;
}

export async function preflight(baseUrl, fetcher = fetch) {
  const origin = customerOrigin(baseUrl);
  const response = await fetcher(origin + '/readyz', {redirect: 'error', signal: AbortSignal.timeout(15000)});
  const status = await response.json();
  if (!response.ok || status.ready !== true || status.public_origin !== origin || !['slack_oauth','slack_events','model'].every(k => status.checks?.[k] === true))
    throw Error('Hosted onboarding is not ready or its public origin differs from the packaged URL. Check /readyz and operator configuration.');
  return origin;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const config = JSON.parse(await readFile(new URL('../service.json', import.meta.url), 'utf8'));
    console.log('Customer service ready:', await preflight(config.baseUrl));
    if (process.argv.includes('--release')) {
      const env = process.env;
      if (!(env.APPLE_KEYCHAIN_PROFILE || (env.APPLE_ID && env.APPLE_APP_SPECIFIC_PASSWORD && env.APPLE_TEAM_ID) || (env.APPLE_API_KEY && env.APPLE_API_KEY_ID && env.APPLE_API_ISSUER)))
        throw Error('Configure operator Apple notarization credentials before releasing.');
      if (!env.CSC_LINK) {
        const identities = execFileSync('security', ['find-identity', '-v', '-p', 'codesigning'], {encoding:'utf8'});
        if (!identities.includes('Developer ID Application:')) throw Error('Install a Developer ID Application identity or configure CSC_LINK. Apple Development is not a distribution identity.');
      }
    }
  } catch (error) {
    console.error('Customer packaging stopped:', error.message);
    process.exitCode = 1;
  }
}
