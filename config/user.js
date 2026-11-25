// Preferenze comuni a tutti i profili dei servizi.
// stream-launcher le riscrive a ogni avvio e aggiunge in coda quelle del servizio
// (pagina iniziale, user agent, proxy).

// --- Pagina iniziale ---
user_pref("browser.startup.page", 1);
user_pref("browser.newtabpage.enabled", false);

// --- DRM Widevine (necessario ai servizi di streaming) ---
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
// Windows: ogni profilo (servizio) è un'icona a sé nella barra delle applicazioni.
user_pref("taskbar.grouping.useprofile", true);
user_pref("browser.warnOnQuit", false);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("full-screen-api.warning.timeout", 0);
user_pref("full-screen-api.transition-duration.enter", "0 0");
user_pref("full-screen-api.transition-duration.leave", "0 0");

// --- Permessi dei siti: rifiutati in automatico, senza finestrelle ---
// Ai servizi di streaming non servono; alcuni (es. Paramount+) chiedono la posizione
// di continuo e la richiesta di Firefox lampeggia. La posizione resta privata anche
// dietro un proxy: il paese lo decide l'IP, non il Wi-Fi.
user_pref("permissions.default.geo", 2);
user_pref("permissions.default.desktop-notification", 2);
user_pref("permissions.default.camera", 2);
user_pref("permissions.default.microphone", 2);
user_pref("permissions.default.xr", 2);
user_pref("geo.provider.network.url", "");
user_pref("dom.push.enabled", false);

// --- Niente richieste da browser ---
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("trailhead.firstrun.didSeeAboutWelcome", true);
user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);
user_pref("browser.translations.automaticallyPopup", false);
user_pref("signon.rememberSignons", true);

// --- Niente modalità provvisoria dopo chiusure forzate ---
// Dopo 3 avvii non chiusi bene Firefox chiede se aprire la modalità provvisoria:
// per un'app di streaming è solo un ostacolo (e senza finestra resta bloccato).
user_pref("toolkit.startup.max_resumed_crashes", -1);

// --- Meno scritture su disco ---
// Su dischi lenti o saturi (HDD, fstrim in corso) ogni fsync blocca Firefox e
// GNOME mostra "l'applicazione non risponde". La cache web va in RAM, la sessione
// si salva di rado (i login stanno nei cookie, non nella sessione) e le liste
// di safebrowsing, che riscrivono decine di MB, non si scaricano.
user_pref("browser.cache.disk.enable", false);
user_pref("browser.cache.memory.enable", true);
user_pref("browser.cache.memory.capacity", 262144);
user_pref("browser.sessionstore.interval", 600000);
user_pref("browser.sessionstore.max_tabs_undo", 0);
user_pref("browser.safebrowsing.malware.enabled", false);
user_pref("browser.safebrowsing.phishing.enabled", false);
user_pref("browser.safebrowsing.downloads.enabled", false);
user_pref("browser.safebrowsing.blockedURIs.enabled", false);
user_pref("browser.safebrowsing.update.enabled", false);
user_pref("places.history.enabled", false);

// --- Addon gestiti dal launcher (cartella extensions del profilo) ---
// Attivati senza conferma; le firme si possono disattivare solo sul runtime ESR.
user_pref("extensions.autoDisableScopes", 0);
user_pref("extensions.enabledScopes", 15);
user_pref("xpinstall.signatures.required", false);
