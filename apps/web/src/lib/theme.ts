/** Theme storage key and the pre-paint script (plain module: safe to import from server components). */
export const THEME_KEY = "srs.theme";

/** Inline script for <head>: applies the stored theme before first paint, so there is no flash. */
export const THEME_BOOT = `try{var t=localStorage.getItem("${THEME_KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;
