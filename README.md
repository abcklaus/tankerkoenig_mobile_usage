# Tankerkönig Mobile

Custom Integration für Home Assistant, die Kraftstoffpreise über die [Tankerkönig-API](https://creativecommons.tankerkoenig.de) abruft – **zuhause** (Standort aus der HA-Konfiguration) und/oder **unterwegs** (Standort eines `device_tracker`, z. B. im Wohnmobil).

- Ausgabe 1:1 im Format von `list.php` (`ok`, `license`, `data`, `status`, `stations`) als Attribute
- Robust gegen fehlende Netzwerkverbindung: bei Fehlern bleibt der letzte Stand erhalten, auch über einen Neustart hinweg
- Zeitpunkt des letzten erfolgreichen Abrufs als Attribut und als eigener Timestamp-Sensor
- Positivliste für Tankstellen (nach ID oder Marke)
- Unterwegs: Abruf im festen Intervall **und** sofort nach einem Ortswechsel (unter Einhaltung der 5-Minuten-Grenze von Tankerkönig)

## Installation (HACS)

1. HACS → ⋮ → *Benutzerdefinierte Repositories* → URL dieses Repositories, Kategorie *Integration*.
2. „Tankerkönig Mobile“ installieren und Home Assistant neu starten.
3. *Einstellungen → Geräte & Dienste → Integration hinzufügen → Tankerkönig Mobile*.

Ohne HACS: den Ordner `custom_components/tankerkoenig_mobile` nach `config/custom_components/` kopieren.

## Einstellungen

| Option | Bedeutung |
|---|---|
| API-Key | Persönlicher Tankerkönig-Key (nur bei der Einrichtung) |
| Preise zuhause / unterwegs | Welche Standorte abgefragt werden |
| Standort Wohnmobil | `device_tracker`, `person` oder `zone` mit `latitude`/`longitude` |
| Suchradius | 1–25 km |
| Kraftstoffart | `diesel`, `e5`, `e10` oder `all` |
| Positivliste | Tankstellen-IDs oder Marken, getrennt durch Komma/Zeilenumbruch. Leer = alle |
| Abrufintervall | Minuten, mindestens 5 (Standard 167 ≈ 10000 s) |
| Neuer Abruf nach Ortswechsel | Strecke in km, ab der unterwegs sofort neu abgefragt wird |

Alles außer dem API-Key lässt sich später über *Konfigurieren* ändern.

## Entities

Pro aktivem Standort ein Gerät („Tankerkönig Zuhause“, „Tankerkönig Unterwegs“) mit:

- **Tankstellen** – State `ok`, sobald Daten vorliegen. Attribute: die API-Antwort (`ok`, `license`, `data`, `status`, `stations` – gefiltert nach Positivliste) plus `last_success`, `last_error`, `query_latitude`, `query_longitude`.
- **Letzter erfolgreicher Abruf** – Timestamp-Sensor (Diagnose).

Schlägt ein Abruf fehl (kein Netz, Timeout, API-Fehler), bleiben State und Attribute unverändert, nur `last_error` wird gesetzt. Danach wird mit Backoff (5, 10, 20 … Minuten, höchstens bis zum normalen Intervall) erneut versucht.

Die großen API-Attribute werden nicht im Recorder gespeichert (16-KB-Limit, Datenbankgröße).

## Umstieg von den `rest:`-Sensoren

1. Integration einrichten.
2. Die alten `rest:`-Einträge aus der `configuration.yaml` entfernen und HA neu starten.
3. Unter *Einstellungen → Entities* die neuen Sensoren auf die bisherigen Entity-IDs umbenennen, z. B.
   - `sensor.tankerkonig_unterwegs_tankstellen` → `sensor.tankerkonig_tankstellen_auswarts`
   - `sensor.tankerkonig_zuhause_tankstellen` → `sensor.tankerkonig_tankstellen_homeregion`

Templates wie `state_attr('sensor.tankerkonig_tankstellen_auswarts', 'stations')` funktionieren danach unverändert.

## Entwicklung

```bash
pip install -r requirements_test.txt
pytest
```

Daten: Tankerkönig, Lizenz CC BY 4.0.
