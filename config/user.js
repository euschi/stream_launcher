// Preferenze del profilo dedicato al client Netflix.
// Vengono riapplicate a ogni avvio.

// --- Pagina iniziale ---
user_pref("browser.startup.homepage", "https://www.netflix.com/browse");
user_pref("browser.startup.page", 1);
user_pref("browser.newtabpage.enabled", false);

// --- DRM Widevine (necessario per Netflix) ---
user_pref("media.eme.enabled", true);
user_pref("media.gmp-widevinecdm.enabled", true);
user_pref("media.gmp-widevinecdm.visible", true);
user_pref("media.gmp-manager.updateEnabled", true);
user_pref("browser.eme.ui.enabled", false);

// --- Rendering / video ---
user_pref("gfx.webrender.all", true);
user_pref("media.autoplay.default", 0);
user_pref("media.hardware-video-decoding.enabled", true);

// --- Interfaccia "da app" ---
user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);
user_pref("browser.tabs.inTitlebar", 0);
user_pref("browser.link.open_newwindow", 1);
user_pref("browser.link.open_newwindow.restriction", 0);
user_pref("browser.tabs.warnOnClose", false);
user_pref("browser.warnOnQuit", false);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("full-screen-api.warning.timeout", 0);
user_pref("full-screen-api.transition-duration.enter", "0 0");
user_pref("full-screen-api.transition-duration.leave", "0 0");

// --- Niente richieste da browser ---
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("trailhead.firstrun.didSeeAboutWelcome", true);
user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);
user_pref("browser.translations.automaticallyPopup", false);
user_pref("signon.rememberSignons", true);
