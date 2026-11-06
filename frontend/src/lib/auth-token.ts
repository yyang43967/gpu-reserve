// The access token lives only in memory. It is never written to localStorage, where any
// injected script could read it; a page reload gets a new one from the refresh cookie.

let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}
