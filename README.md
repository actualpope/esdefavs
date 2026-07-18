# EmuDeck Favorites Sync 0.7.2

Et lite Steam Deck-program som syncer ES-DE-favoritter til Steam.

Når du trykker `Oppdater ES-DE favoritter` i kontrollpanelet, gjør programmet tre ting:

1. Leser favoritter fra ES-DE `gamelist.xml`.
2. Lager/oppdaterer egne SRM Manual-parsere med navn som `ES-DE Favorites Sync - ...`.
3. Hvis Steam er lukket, kjører det Steam ROM Manager `remove` og deretter `add` for våre parsere.

Programmet oppdaterer ikke Steam i bakgrunnen på egen hånd. Du må trykke `Oppdater ES-DE favoritter` selv hver gang du har endret favoritter i ES-DE.

Hvis EmuDeck er installert på SD-kort, leter programmet også etter SRM direkte under for eksempel:

```text
/run/media/deck/FF4Y7/Emulation/tools/
```

## Installer uten git

Du trenger ikke `git` på Steam Deck. Last ned ZIP fra GitHub én gang, installer, og bruk deretter kontrollpanelets oppdateringsknapp.

1. Åpne denne lenken i browser på Steam Deck:

```text
https://github.com/actualpope/esdefavs/archive/refs/heads/main.zip
```

2. Pakk ut ZIP-en, for eksempel i `Downloads`.
3. Åpne den utpakkede mappen i Dolphin.
4. Dobbeltklikk `EmuDeck Favorites Sync.desktop`.
5. Hvis programmet spør om å installere: velg ja.

Du kan også installere fra Konsole etter at ZIP-en er pakket ut:

```bash
cd ~/Downloads/esdefavs-main
bash install.sh
```

Etterpå åpner du kontrollpanelet herfra:

```text
~/.local/share/emudeck-favorites-sync/EmuDeck Favorites Sync.desktop
~/Desktop/EmuDeck Favorites Sync.desktop
```

Du kan også åpne desktop-filen direkte fra mappen over i Dolphin.

## Oppdater senere

Når det kommer nye endringer på GitHub, kan du oppdatere fra kontrollpanelet:

```text
Oppdater program
```

Eller fra terminal:

```bash
bash ~/.local/share/emudeck-favorites-sync/update.sh
```

Oppdatering fungerer både med og uten `git`. Hvis `git` ikke finnes, laster programmet ned ny GitHub-ZIP automatisk.

Hvis du senere installerer fra en git-klone, kan du også kjøre:

```bash
bash update.sh
```

Etter installering ligger kontrollpanelet også her:

```text
~/.local/share/emudeck-favorites-sync/EmuDeck Favorites Sync.desktop
```

## Bruk

Åpne `EmuDeck Favorites Sync.desktop`.

Der får du en enkel meny:

- **Oppdater ES-DE favoritter** — leser gjeldende favoritter fra ES-DE og synkroniserer dem til Steam via SRM, hvis Steam er lukket.
- **Se ES-DE Favoritter** — viser en ren liste over hvilke spill som er favorittmerket akkurat nå. Endrer ingenting.
- **Oppdater program** — henter siste versjon fra GitHub.
- **Reset (fjern alt fra Steam/SRM)** — viser først nøyaktig hva som vil bli fjernet, ber om bekreftelse, og fjerner deretter alle `ES-DE Favorites Sync`-parsere, manifester og Steam-snarveier programmet har laget. Rører ikke ES-DE sine egne favoritter eller andre SRM-parsere.

Vanlig bruk er bare:

1. Favorittmarker eller fjern favoritt på spill i ES-DE.
2. Lukk Steam helt.
3. Åpne kontrollpanelet og trykk `Oppdater ES-DE favoritter`.
4. Start Steam igjen.

Programmet kjører ikke i bakgrunnen og oppdager ikke endringer av seg selv — alt skjer når du trykker `Oppdater ES-DE favoritter`.

## Hvordan "Oppdater ES-DE favoritter" fungerer

- Trykk på knappen leser ES-DE sine gamelists på nytt hver gang, uansett om noe har endret seg siden sist.
- Hvis Steam kjører, blokkeres synkroniseringen og du får beskjed om å lukke Steam og prøve igjen.
- Når Steam er lukket, kjører programmet først SRM `remove` på eksisterende favorittsync-parsere.
- Deretter fjerner programmet gamle Steam-shortcuts som ikke lenger finnes i ES-DE favorites.
- Til slutt skriver programmet ny SRM-staging og kjører SRM `add` på dagens favoritter.
- Fjerning matcher SRM-shortcuts tolerant, slik at små forskjeller i anførselstegn og `LaunchOptions` ikke hindrer cleanup.
- Mens SRM `add` kjøres, aktiveres bare `ES-DE Favorites Sync`-parserne midlertidig; andre SRM-parsere settes tilbake slik de var etterpå.
- Spillene legges både i en samlet `ES-DE Favorites`-collection og i konsollens vanlige SRM-collection.
- Favorittsync-parserne blir liggende i SRM selv om en konsoll akkurat nå har null favoritter; manifestet blir bare tomt.

## Hvis programmet ikke finner Steam ROM Manager

Fra terminal:

```bash
~/.local/bin/emudeck-favorites-sync set-srm-path "/path/to/Steam-ROM-Manager.AppImage"
```

Programmet husker valget under:

```text
~/.local/state/emudeck-favorites-sync/srm-app-path.txt
```

Deretter kan du prøve `Oppdater ES-DE favoritter` igjen.

## Sikkerhet

- Programmet skriver ikke mens Steam kjører.
- Programmet lager backup før SRM-konfig og Steam-shortcuts endres.
- Eksisterende EmuDeck-parsere røres ikke.
- Eksisterende Steam-shortcuts bevares.
- Global SRM `nuke` brukes aldri.
- Hvis `deleteDisabledShortcuts` er aktivert i SRM, blokkeres SRM-staging.

## Terminalkommandoer hvis du trenger dem

De to vanligste, som tilsvarer de to første knappene i kontrollpanelet:

```bash
~/.local/bin/emudeck-favorites-sync list-favorites
~/.local/bin/emudeck-favorites-sync autosync-now --summary
```

Øvrige kommandoer, for feilsøking eller avansert bruk:

```bash
~/.local/bin/emudeck-favorites-sync autosync-status
~/.local/bin/emudeck-favorites-sync srm-remove-now
~/.local/bin/emudeck-favorites-sync srm-add-now
~/.local/bin/emudeck-favorites-sync set-srm-path "/path/to/Steam-ROM-Manager.AppImage"
~/.local/bin/emudeck-favorites-sync steam-import-now
~/.local/bin/emudeck-favorites-sync reset            # forhåndsvisning, endrer ingenting
~/.local/bin/emudeck-favorites-sync reset --confirm  # fjerner faktisk
```

Programmet installeres uten bakgrunnstjeneste. `autosync-on`/`autosync-off` finnes fortsatt i CLI-en for den som selv vil eksperimentere med en bakgrunnstjeneste som oppdager endringer og kjører `esde-closed` automatisk, men dette er ikke standardoppsettet og vises ikke i kontrollpanelet.

## Feilsøking

State, logg og backups ligger under:

```text
~/.local/state/emudeck-favorites-sync
```

Autosync-loggen ligger her:

```text
~/.local/state/emudeck-favorites-sync/autosync.log
```

## Avinstaller

Fra den utpakkede mappen:

```bash
bash uninstall.sh
```
