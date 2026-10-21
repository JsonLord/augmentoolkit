// Operator credentials stay in memory and are sent only to this application's origin.
let operatorToken = '';
export const setOperatorToken = (value) => { operatorToken = value; };
export function authenticatedFetch(input, options = {}) {
  const url = new URL(input, window.location.origin);
  if (url.origin !== window.location.origin) {
    throw new Error('Operator credentials require the application origin');
  }
  const headers = new Headers(options.headers);
  if (operatorToken) headers.set('Authorization', `Bearer ${operatorToken}`);
  return fetch(url, { ...options, headers, redirect: 'error' });
}
