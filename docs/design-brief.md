EmuDeck Favorites Sync — ideell funksjon og systematikk
Dato: 2026-07-07

Formål
======

Programmet skal gjøre én konkret ting:

Når brukeren markerer eller fjerner favoritter i ES-DE på Steam Deck, skal Steam-biblioteket senere gjenspeile dette via Steam ROM Manager (SRM), uten at brukeren må åpne terminal eller manuelt kjøre add/remove-operasjoner.

Målet er ikke å erstatte EmuDeck, ES-DE eller SRM. Målet er å være et tynt, trygt bindeledd mellom:

1. ES-DE sin favorittstatus i gamelist.xml
2. SRM sine parsere/manual manifests
3. Steam sitt non-Steam library / shortcuts.vdf


Viktig grunnprinsipp
===================

Programmet skal ikke sjekke hele tiden.

Den riktige modellen er event/pending-basert:

1. ES-DE lukkes.
2. Da kjøres en trigger: esde-closed.
3. Programmet leser ES-DE-favoritter én gang.
4. Hvis favorittlisten har endret seg, lagres ønsket tilstand og sync markeres som pending.
5. Hvis Steam er trygt lukket, kan programmet kjøre oppdateringen med én gang.
6. Hvis Steam kjører, skal programmet ikke skrive til Steam/SRM der og da. Det skal bare lagre pending.
7. Neste gang programmet får en trygg anledning, for eksempel ved startup/pending-check eller manuell “Oppdater Steam nå”, behandles pending.

Programmet skal altså ikke være en kontinuerlig poller/timer som sjekker hvert X sekund.


Overordnet flyt
===============

Ideell flyt ser slik ut:

Bruker spiller / blar i ES-DE
    ↓
Bruker legger til eller fjerner favoritter
    ↓
ES-DE lagrer gamelist.xml når ES-DE avsluttes
    ↓
ES-DE-lukkehook kaller:
    ~/.local/bin/emudeck-favorites-sync esde-closed
    ↓
Programmet scanner ES-DE gamelists
    ↓
Programmet sammenligner ny favorittsignatur med sist kjente signatur
    ↓
Hvis ingen relevant endring:
    status = no-change
    ferdig
    ↓
Hvis endring:
    desired.json oppdateres
    autosync.json pending = true
    last_change_detected_at settes
    ↓
Hvis Steam kjører:
    status = pending-steam-running
    ferdig, ikke skriv til Steam/SRM
    ↓
Hvis Steam er lukket:
    kjør full reconcile:
        1. les eksisterende SRM manual entries
        2. kjør SRM remove på våre egne parsere
        3. skriv nye SRM manual manifests/parsers
        4. fjern stale Steam-shortcuts direkte hvis nødvendig
        5. kjør SRM add på våre egne parsere
        6. lagre last-srm-entries.json
        7. pending = false


Hva “pending” betyr
===================

pending betyr:

“ES-DE-favorittene har en ønsket tilstand som ennå ikke sikkert er reflektert i Steam.”

pending skal bli true når:

- ES-DE-lukkehook oppdager at favorittlisten har endret seg.
- Programmet ser at Steam-biblioteket ikke matcher ønsket tilstand.
- Manuell “Oppdater Steam nå” kjøres og tvinger reconcile.

pending skal bli false først når:

- SRM remove/add og direkte cleanup har kjørt ferdig uten blokkering.
- last-srm-entries.json er oppdatert med dagens ønskede SRM entries.

pending skal IKKE slettes bare fordi:

- ES-DE-listen ikke har endret seg siden sist.
- SRM staging allerede finnes.
- Programmet tror det “burde” være oppdatert.

Steam-biblioteket må faktisk være forsøkt oppdatert.


Hvor data lagres
================

Installerte programfiler:

~/.local/share/emudeck-favorites-sync/

CLI:

~/.local/bin/emudeck-favorites-sync

State:

~/.local/state/emudeck-favorites-sync/

Viktige state-filer:

~/.local/state/emudeck-favorites-sync/autosync.json
    Holder autosync-status:
    - enabled
    - pending
    - last_signature
    - last_check_at
    - last_esde_closed_at
    - last_steam_running
    - last_change_detected_at
    - last_sync_at
    - last_srm_add_at
    - last_srm_remove_at
    - last_result
    - last_error
    - favorites

~/.local/state/emudeck-favorites-sync/desired.json
    Programmet sin sist ønskede favorittliste fra ES-DE.

~/.local/state/emudeck-favorites-sync/last-srm-entries.json
    Sist kjente SRM manual entries som programmet mener ble brukt til Steam.
    Denne brukes til å vite hvilke gamle spill som skal fjernes når de ikke lenger er favoritter.

~/.local/state/emudeck-favorites-sync/autosync.log
    Logg over triggere og sync-forsøk.

~/.local/state/emudeck-favorites-sync/srm-app-path.txt
    Valgfri manuelt valgt path til Steam ROM Manager AppImage.


ES-DE-inndata
=============

Programmet leser ES-DE sine gamelist.xml-filer.

Typisk plassering:

~/ES-DE/gamelists/<system>/gamelist.xml

ROM-plassering oppdages fra ES-DE settings:

~/ES-DE/es_settings.xml

eller fra kjente EmuDeck-plasseringer, for eksempel:

~/Emulation/roms
/run/media/deck/<SD-KORT>/Emulation/roms

I brukerens konkrete oppsett kan EmuDeck/SRM ligge på SD-kort, for eksempel:

/run/media/deck/FF4Y7/Emulation/tools/

ROMs kan ligge på microSD, mens programmet selv og ES-DE-data ligger internt.


SRM-modellen
============

Programmet lager egne SRM Manual-parsere.

Parserne skal hete:

ES-DE Favorites Sync - <konsollnavn>

Parser-ID skal være eid av programmet og tydelig identifiserbar, slik at programmet aldri endrer vanlige EmuDeck-parsere.

Programmet skal:

- lese eksisterende SRM parser for samme system
- kopiere relevante emulator-/launch-innstillinger
- lage en Manual-parser for favoritter
- skrive manual manifest med dagens favorittspill
- bevare parseren selv om systemet akkurat nå har null favoritter
- skrive tom manifestliste i stedet for å slette parseren

Hvor SRM-data ligger:

~/.config/steam-rom-manager/userData/

Viktige filer/mapper:

~/.config/steam-rom-manager/userData/userConfigurations.json
~/.config/steam-rom-manager/userData/manualManifests/emudeck-favorites-sync/


Collections
===========

Programmet skal lage en samlet Steam collection kalt “ES-DE Favorites”.

Spillene skal samtidig havne i konsollens vanlige SRM-collection, slik SRM ellers organiserer biblioteket.

Eksempel:

Riktig:
    ES-DE Favorites
    Nintendo Game Boy Advance
    Nintendo GameCube

Hvis eksisterende programskapte parsere mangler “ES-DE Favorites” som ekstra collection, skal programmet legge den til igjen på våre egne parsere.


Add/remove må være én samlet “oppdater”
======================================

Brukeren skal ikke måtte tenke på “add SRM” og “remove SRM” som separate handlinger.

Riktig modell er:

Oppdater Steam nå
    = full reconcile

Full reconcile betyr:

1. Finn forrige kjente favoritt-SRM entries.
2. Finn dagens ønskede favoritt-SRM entries.
3. Kjør SRM remove for våre egne parsere.
4. Stage dagens manifests/parsers.
5. Fjern stale Steam-shortcuts som ikke lenger er favoritter.
6. Kjør SRM add for dagens favoritter.
7. Lagre dagens entries som forrige entries til neste gang.


Hvorfor direkte Steam-cleanup trengs
====================================

SRM remove alene har vist seg å ikke alltid være nok.

Mulig årsak:

Hvis parser/manifest endres eller fjernes før SRM remove har nok kontekst, kan SRM miste oversikten over hvilke gamle Steam-shortcuts den skal fjerne.

Derfor må programmet selv kunne lese Steam shortcuts og fjerne gamle favorittspill direkte når de ikke lenger er i ønsket favorittliste.

Steam shortcuts ligger typisk her:

~/.local/share/Steam/userdata/<steam-user-id>/config/shortcuts.vdf
~/.steam/steam/userdata/<steam-user-id>/config/shortcuts.vdf
~/.var/app/com.valvesoftware.Steam/.local/share/Steam/userdata/<steam-user-id>/config/shortcuts.vdf

Programmet må aldri gjøre global nuke.

Programmet skal bare fjerne shortcuts det kan matche mot tidligere programskapte favorittentries.


Matching av Steam-shortcuts
===========================

Matching kan ikke være for streng.

SRM kan skrive shortcuts.vdf med andre anførselstegn, litt annen Exe-format eller LaunchOptions-format enn programmet selv forventer.

Derfor skal cleanup matche tolerant:

- spillnavn må stemme
- normalisert LaunchOptions / ROM-path må stemme
- forskjeller i anførselstegn og slash-retning skal tolereres

Det er ikke nok å matche bare på tittel, fordi vanlige Steam-spill eller flere emulatorvarianter kan ha samme navn.

Det er heller ikke trygt å kreve intern “ES-DE Favorites Sync”-tag, fordi SRM ikke nødvendigvis skriver den slik programmet forventer.


Steam-sikkerhet
===============

Programmet skal ikke skrive til Steam shortcuts mens Steam kjører.

Hvis Steam kjører:

- ikke skriv SRM staging som forutsetter lukket Steam
- ikke skriv shortcuts.vdf
- sett pending true
- last_result = pending-steam-running

Hvis Steam ikke kjører:

- full reconcile kan kjøres

Steam-detektering bør se etter prosesser som:

- steam
- steamwebhelper


Manuell “Oppdater Steam nå”
===========================

Manuell knapp skal alltid tvinge full reconcile.

Den skal ikke hoppe over fordi favorittsignaturen er uendret.

Hvorfor:

Steam-biblioteket kan være ute av sync selv om ES-DE-favorittene ikke har endret seg siden sist. For eksempel:

- tidligere sync feilet
- bruker fjernet spill manuelt i Steam
- SRM add/remove gjorde bare deler av jobben
- programmet ble oppgradert midt i pending-state

Derfor:

autosync-now = force=True


Automatisk ES-DE-trigger
========================

Den ideelle triggeren er:

~/.local/bin/emudeck-favorites-sync esde-closed

eller wrapper-scriptet:

~/.local/share/emudeck-favorites-sync/esde-closed.sh

Denne skal kjøres hver gang ES-DE faktisk avsluttes.

Viktig:

Programmet kan ikke magisk vite at ES-DE ble lukket uten en kobling.

Det må finnes én av disse:

1. ES-DE startes gjennom en wrapper som gjør:

       start ES-DE
       vent til ES-DE avsluttes
       kjør emudeck-favorites-sync esde-closed

2. ES-DE/EmuDeck har en exit-hook hvor esde-closed.sh kan registreres.

3. Steam-shortcuten for “Emulation Station” endres til å peke på en wrapper.

Uten en slik kobling vil automatikk ikke skje, men manuell “Oppdater Steam nå” vil fortsatt fungere.


Ønsket wrapper-modell
=====================

En ideell wrapper for ES-DE bør gjøre dette:

#!/usr/bin/env bash
set -euo pipefail

start faktisk ES-DE med alle originale argumenter
vent på at ES-DE avsluttes
kjør:
    ~/.local/bin/emudeck-favorites-sync esde-closed

Hvis Steam fortsatt kjører når wrapperen avsluttes:

- programmet skal bare lagre pending

Hvis Steam er lukket:

- programmet kan kjøre reconcile

Wrapperen bør ikke endre ES-DE sine filer eller EmuDeck-konfig mer enn nødvendig.


Startup/pending-service
=======================

Autosync-on kan installere en liten systemd user service.

Denne skal ikke være en kontinuerlig watcher.

Den skal være en oneshot startup/pending-check:

- kjør når bruker-session starter
- sjekk om pending finnes
- hvis Steam er lukket, behandle pending
- hvis Steam kjører, behold pending

Formålet er å håndtere “neste gang Steam Deck/desktop-session får mulighet”, ikke å polle hvert 20. sekund.


GUI
===

Programmet har et enkelt kontrollpanel:

- Skru på automatisk sync
- Skru av automatisk sync
- Status og favoritter
- Oppdater Steam nå
- Velg SRM AppImage
- Importer stagede favoritter til Steam nå
- Lag feilsøkingsrapport

Status bør vise:

- Autosync ON/OFF
- Service status
- Pending sync yes/no
- Last ES-DE close
- Last check
- Steam seen running/stopped/unknown
- Last change
- Last sync
- Last remove
- Last SRM add
- Last result
- Last error
- Current favorites
- State/log/service file paths


Hva som fungerer nå
===================

Manuell “Oppdater Steam nå” fungerer som ønsket:

- legger til nye favoritter
- fjerner gamle favoritter
- lager samlet “ES-DE Favorites”-collection
- bruker også konsollens SRM collection
- matcher SRM-shortcuts mer tolerant ved fjerning


Hva som fortsatt må løses
=========================

Den gjenstående hovedoppgaven er ikke add/remove-motoren.

Den gjenstående hovedoppgaven er trigger-koblingen:

Hvordan kobler vi ES-DE-lukking til:

~/.local/bin/emudeck-favorites-sync esde-closed

Dette avhenger av hvordan ES-DE startes på brukerens Steam Deck:

- via Steam-shortcuten “Emulation Station”
- via EmuDeck
- via en AppImage direkte
- via Steam Rom Manager-generert non-Steam shortcut

Neste utviklingssteg bør være å lese/rapportere faktisk Steam-shortcut for ES-DE og/eller EmuDeck sin ES-DE launch-fil, og så lage en trygg wrapper rundt den.


Feil retning som ikke skal brukes
=================================

Ikke bruk periodisk systemd timer som sjekker hvert 30. sekund.

Ikke bruk kontinuerlig Python-loop som sjekker hvert 20. sekund.

Ikke gjør global SRM nuke.

Ikke fjern vanlige Steam-spill basert på tittel alene.

Ikke slett SRM-parsere bare fordi en konsoll har null favoritter akkurat nå.


Kort oppsummert for en AI
=========================

Dette programmet skal være en eventdrevet pending/reconcile-motor:

Event:
    ES-DE lukket

Capture:
    Les ES-DE gamelist.xml og lag ønsket favorittstate

Queue:
    Hvis endret, sett pending

Safety gate:
    Ikke skriv når Steam kjører

Reconcile:
    Når Steam er lukket, kjør SRM remove + stage manifests/parsers + direct stale shortcut cleanup + SRM add

Persist:
    Lagre last-srm-entries og pending=false

Manual override:
    Oppdater Steam nå tvinger full reconcile uansett signatur

Missing piece:
    ES-DE-lukking må kobles til esde-closed via wrapper eller hook
