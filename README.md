# netflix_client

Client Netflix desktop per Linux basato sul motore di Firefox (Gecko), senza
bisogno di avere Firefox installato.

Su Chrome/PWA con GPU NVIDIA e Wayland i video DRM restano neri (o, togliendo
l'accelerazione, la qualità crolla); Firefox invece li riproduce correttamente.
Questo progetto usa un **Firefox portatile e isolato** come runtime: profilo
dedicato, nessuna scheda o barra, finestra e icona proprie.

## Installazione

```bash
./install.sh              # scarica il runtime e registra l'app
./install.sh --update     # riscarica il runtime Firefox
./install.sh --uninstall  # rimuove tutto
```

Poi avvia **Netflix** dal menu applicazioni oppure `netflix-client` da terminale.

Il runtime e il profilo stanno in `~/.local/share/netflix-client/`.

## Struttura

| File | Ruolo |
|---|---|
| `install.sh` | scarica il runtime, copia la configurazione, crea launcher e voce di menu |
| `netflix-client` | launcher (Wayland nativo, `app_id` dedicato) |
| `config/user.js` | preferenze: DRM Widevine, WebRender, homepage, niente onboarding |
| `config/chrome/userChrome.css` | nasconde schede e barre |
| `config/policies.json` | policy del runtime: telemetria off, DRM sempre attivo |
| `config/icon.svg` | icona generica di riserva |

L'icona Netflix (marchio registrato) non è inclusa nel repo: viene scaricata
dai server Netflix durante l'installazione.

## Note

- Al primo avvio Firefox scarica Widevine in background: se il video non parte
  subito, attendi un minuto e riapri.
- Su Linux Netflix limita lo streaming a 720p (Widevine L3).
- Indietro: **Alt+←**. Schermo intero: **F11**.
