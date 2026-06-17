# Konzeptabgabe

**Themenbereich**: LLM Performance Vergleich (Feature Realisation)
**Werkzeug**: [CASCADE](https://github.com/TobiasKiecker/CASCADE)
**Gruppenmitglieder**: Bruno Vincent Hemoura, Alexander Hristov, Caspar Moritz Klein
**Gruppe**: 01 

# Forschungsfrage

Wie kann CASCADE so erweitert werden, dass LLM-Nutzung und Laufzeitverhalten der Pipeline strukturiert erfasst werden, um verschiedene Modelle, Prompts und Ausführungsschritte vergleichbar zu machen?

# Abstract


CASCADE generiert und validiert Tests mithilfe einer mehrstufigen Pipeline aus Extraktion, Analyse, Generierung und Ausführung. Für einen fundierten LLM-Performance-Vergleich fehlte bisher eine zentrale Erfassung von Tokenverbrauch, Laufzeiten und Fehlern. Im Rahmen dieser Feature-Realisierung wurde ein leichtgewichtiger Metrics-Mechanismus für `src/cascade` implementiert. Er protokolliert Events als JSON Lines, erzeugt am Ende eines Runs eine Zusammenfassung und misst gezielt LLM-Aufrufe sowie zentrale Pipeline-Schritte.

# Motivation

LLM-basierte Testgenerierung ist stark abhängig von Modell, Prompting, Eingabegröße und Ausführungsumgebung. Ohne Metriken ist schwer nachvollziehbar, ob ein Modell tatsächlich bessere Ergebnisse liefert oder nur höhere Kosten bzw. längere Laufzeiten verursacht. Eine reproduzierbare Messgrundlage hilft, Tokenverbrauch, Dauer, Fehler und Teilschritte der CASCADE-Pipeline objektiv zu vergleichen.

# Lösungsansatz

Die Umsetzung basiert auf einem zentralen `MetricsRecorder` in `src/cascade/utils/Metrics.py`. Pro Run wird ein Recorder initialisiert, der Events in eine JSONL-Datei schreibt und aggregierte Kennzahlen in einer Summary speichert. LLM-Nutzung wird aus OpenAI-kompatiblen Response-Objekten normalisiert, insbesondere Input-, Output- und Total-Tokens.

Für die Instrumentierung gibt es zwei Hauptwege: `record_metric_event(...)` protokolliert explizite Ereignisse und Metadaten, während `time_block(...)` die Laufzeit eines Codeblocks misst. Über `ContextVar` werden der aktive Recorder und Kontextinformationen wie Phase, Methode oder Sample verwaltet, sodass sie nicht durch alle Methodenparameter weitergereicht werden müssen. Instrumentiert wurden vor allem Pipeline-Ablauf, Generierung, Analyse, Java-Ausführung, Docker-Aufrufe und OpenAI-Requests.

# Skizze

> [!note]
> TODO
