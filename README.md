# Tankerkönig Mobile

Custom Integration für Home Assistant, die Kraftstoffpreise über die [Tankerkönig-API](https://creativecommons.tankerkoenig.de) abruft – **zuhause** (Standort aus der HA-Konfiguration) und/oder **unterwegs** (Standort eines `device_tracker`, z. B. im Wohnmobil).

- Ausgabe 1:1 im Format von `list.php` (`ok`, `license`, `data`, `status`, `stations`) als Attribute
- Robust gegen fehlende Netzwerkverbindung: bei Fehlern bleibt der letzte Stand erhalten, auch über einen Neustart hinweg
- Zeitpunkt des letzten erfolgreichen Abrufs als Attribut und als eigener Timestamp-Sensor
- Positivliste für Tankstellen zuhause (nach ID oder Marke)
- Unterwegs: Abruf, sobald das Wohnmobil eine einstellbare Zeit steht, danach im festen Intervall; keine Abrufe während der Fahrt oder in der Daheim-Zone
- Feste Abrufzeiten (z. B. wenn Preise zu festen Uhrzeiten wechseln)
- Eigenes Integrations-Icon (ab Home Assistant 2026.3)

## Installation (HACS)

1. HACS → ⋮ → *Benutzerdefinierte Repositories* → URL dieses Repositories, Kategorie *Integration*.
2. „Tankerkönig Mobile“ installieren und Home Assistant neu starten.
3. *Einstellungen → Geräte & Dienste → Integration hinzufügen → Tankerkönig Mobile*.

Ohne HACS: den Ordner `custom_components/tankerkoenig_mobile` nach `config/custom_components/` kopieren.

## Einstellungen

**Allgemein**

| Option | Bedeutung |
|---|---|
| API-Key | Persönlicher Tankerkönig-Key (nur bei der Einrichtung) |
| Kraftstoffart | `diesel`, `e5` oder `e10` (eine Sorte pro Integration) |
| Suchradius | 1–25 km, gilt für zuhause und unterwegs |
| Abrufintervall | Minuten, mindestens 5 (Standard 167 ≈ 10000 s). Unterwegs nur, solange das Wohnmobil steht |
| Feste Abrufzeiten | Uhrzeiten im **24-Stunden-Format `HH:MM`** (z. B. `06:00, 12:00, 18:30`), getrennt durch Komma/Zeilenumbruch. Zu diesen Zeiten wird auf jeden Fall abgerufen – unterwegs nur im Stand |

**Abschnitt „Zuhause“**

| Option | Bedeutung |
|---|---|
| Preise zuhause abfragen | Standort aus der HA-Konfiguration |
| Positivliste Tankstellen (nur zuhause) | Tankstellen-IDs oder Marken, getrennt durch Komma/Zeilenumbruch. Leer = alle. Unterwegs wird **nicht** gefiltert |

**Abschnitt „Unterwegs“**

| Option | Bedeutung |
|---|---|
| Preise unterwegs abfragen | |
| Standort Wohnmobil | `device_tracker`, `person` oder `zone` mit `latitude`/`longitude` |
| Daheim-Zone | Zone (Standard `zone.home`), in der für unterwegs nicht abgerufen wird. Beim Einfahren werden sofort die Preise für zuhause abgerufen |
| Standzeit bis zum Abruf | Minuten am gleichen Ort, bevor abgerufen wird (Standard 10) |
| Standort-Toleranz | Bewegungen darunter (GPS-Ungenauigkeit) zählen nicht als Ortswechsel (Standard 200 m) |

Alles außer dem API-Key lässt sich später über *Konfigurieren* ändern.

### Abruflogik unterwegs

1. Bewegt sich das Wohnmobil weiter als die Standort-Toleranz, gilt es als **in Fahrt**: keine Abrufe, auch nicht zu festen Zeiten.
2. Steht es die eingestellte **Standzeit** am gleichen Ort, wird abgerufen. Danach gilt das Abrufintervall (und die festen Zeiten), bis es wieder losfährt.
3. In der **Daheim-Zone** wird nie für unterwegs abgerufen. Beim Verlassen beginnt die Fahrt (Abruf erst nach der Standzeit).

Nach einem Update: Home Assistant neu starten und den Browser neu laden, damit die neuen Beschriftungen angezeigt werden.

## Entities

Pro aktivem Standort ein Gerät („Tankerkönig Zuhause“, „Tankerkönig Unterwegs“) mit:

- **Tankstellen** – State `ok`, sobald Daten vorliegen. Attribute: die API-Antwort (`ok`, `license`, `data`, `status`, `stations` – zuhause gefiltert nach Positivliste) plus `last_success`, `last_error`, `query_latitude`, `query_longitude` sowie unterwegs `paused_in_zone` (Wohnmobil in der Daheim-Zone) und `vehicle_moving` (Wohnmobil in Fahrt).
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
