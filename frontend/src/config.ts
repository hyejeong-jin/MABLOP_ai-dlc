// Service-wide constants shown in the UI.
export const CONTACT_EMAIL = "hyejeong.data@gmail.com";
export const DEVELOPER = "hyejeong.jin";

/** Prefix for files in /public, valid under any Vite base path (for example GitHub Pages). */
export function asset(path: string): string {
  return `${import.meta.env.BASE_URL}${path.replace(/^\//, "")}`;
}