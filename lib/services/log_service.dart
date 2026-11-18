// WHAT THIS FILE DOES
// Everything that is SAVED, all on this phone only (SharedPreferences):
// - LogService: the detection history (symptom + time).
// - Reflections: the kept reframing journal entries.
// - buildReport(): the readable .txt report for the Download button.
// No video or images are ever saved.

import 'dart:io';
import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'dart:convert';
import 'package:intl/intl.dart';
import 'symptom_data.dart';

/// Saves a text file on this phone where the user can open it
/// (Download/Calm Kaba on Android) and returns where it went.
Future<String> saveTextFile(String name, String text) async {
  try {
    return (await const MethodChannel(
      'calm/detection',
    ).invokeMethod<String>('saveText', {'name': name, 'text': text}))!;
  } on MissingPluginException {
    // Non-Android builds and tests have no native side.
    final directory = await getApplicationDocumentsDirectory();
    return (await File('${directory.path}/$name').writeAsString(text)).path;
  }
}

/// File-name-safe local time, e.g. 2026-11-02_14-05-09.
String fileStamp(DateTime t) => t
    .toIso8601String()
    .substring(0, 19)
    .replaceAll(':', '-')
    .replaceAll('T', '_');

typedef Reflection = ({DateTime time, List<String> answers});

/// Reframing entries the user chose to keep, stored only on this phone.
class Reflections {
  static const _key = 'reframing_entries';

  /// Newest first.
  static Future<List<Reflection>> load() async {
    final prefs = await SharedPreferences.getInstance();
    return [
      for (final raw in (prefs.getStringList(_key) ?? []).reversed)
        () {
          final map = jsonDecode(raw) as Map<String, dynamic>;
          return (
            time: DateTime.parse(map['time'] as String),
            answers: List<String>.from(map['answers'] as List),
          );
        }(),
    ];
  }

  static Future<void> add(DateTime time, List<String> answers) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setStringList(_key, [
      ...?prefs.getStringList(_key),
      jsonEncode({'time': time.toIso8601String(), 'answers': answers}),
    ]);
  }

  /// Replaces the entry kept at [time], or deletes it when [answers] is null.
  static Future<void> replace(DateTime time, List<String>? answers) async {
    final prefs = await SharedPreferences.getInstance();
    final key = time.toIso8601String();
    await prefs.setStringList(_key, [
      for (final raw in prefs.getStringList(_key) ?? [])
        if (jsonDecode(raw)['time'] != key)
          raw
        else if (answers != null)
          jsonEncode({'time': key, 'answers': answers}),
    ]);
  }
}

class LogEntry {
  final String symptom;
  final DateTime timestamp;
  final double? score;
  final double? threshold;
  final String? modelRun;

  LogEntry({
    required this.symptom,
    required this.timestamp,
    this.score,
    this.threshold,
    this.modelRun,
  });

  Map<String, dynamic> toMap() => {
    'symptom': symptom,
    'timestamp': timestamp.toIso8601String(),
    if (score != null) 'score': score,
    if (threshold != null) 'threshold': threshold,
    if (modelRun != null) 'modelRun': modelRun,
  };

  factory LogEntry.fromMap(Map<String, dynamic> map) => LogEntry(
    symptom: map['symptom'],
    timestamp: DateTime.parse(map['timestamp']),
    score: (map['score'] as num?)?.toDouble(),
    threshold: (map['threshold'] as num?)?.toDouble(),
    modelRun: map['modelRun'] as String?,
  );
}

class LogService {
  static const String _logKey = 'detection_logs';
  static final LogService _instance = LogService._internal();

  factory LogService() => _instance;

  LogService._internal();

  Future<void> _writes = Future.value();

  Future<void> addLog(
    String symptom, {
    double? score,
    double? threshold,
    String? modelRun,
  }) {
    final write = _writes.then(
      (_) => _addLog(symptom, score, threshold, modelRun),
    );
    _writes = write.catchError((Object _) {});
    return write;
  }

  Future<void> _addLog(
    String symptom,
    double? score,
    double? threshold,
    String? modelRun,
  ) async {
    final prefs = await SharedPreferences.getInstance();
    final logs = await _readLogs();
    logs.add(
      LogEntry(
        symptom: symptom,
        timestamp: DateTime.now(),
        score: score,
        threshold: threshold,
        modelRun: modelRun,
      ),
    );

    final List<String> encodedLogs = logs
        .map((log) => jsonEncode(log.toMap()))
        .toList();
    await prefs.setStringList(_logKey, encodedLogs);
  }

  Future<List<LogEntry>> getLogs() async {
    await _writes;
    return _readLogs();
  }

  Future<List<LogEntry>> _readLogs() async {
    final prefs = await SharedPreferences.getInstance();
    final List<String>? encodedLogs = prefs.getStringList(_logKey);

    if (encodedLogs == null) return [];

    return encodedLogs.map((log) => LogEntry.fromMap(jsonDecode(log))).toList();
  }

  Future<void> clearLogs() {
    final write = _writes.then((_) async {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(_logKey);
    });
    _writes = write.catchError((Object _) {});
    return write;
  }

  /// Returns where the file was saved.
  Future<String> exportLogs() async {
    final now = DateTime.now();
    return saveTextFile(
      'calm_kaba_results_${fileStamp(now)}.txt',
      buildReport(await getLogs(), now),
    );
  }

}

/// The downloaded results: a plain-language summary first, then every detection by day.
String buildReport(List<LogEntry> logs, DateTime now) {
  final day = DateFormat('MMMM d, y');
  final time = DateFormat('h:mm a');
  final sorted = [...logs]..sort((a, b) => a.timestamp.compareTo(b.timestamp));
  final counts = <String, int>{};
  for (final log in sorted) {
    counts[log.symptom] = (counts[log.symptom] ?? 0) + 1;
  }
  final ranked = counts.entries.toList()..sort((a, b) => b.value.compareTo(a.value));
  final out = StringBuffer()
    ..writeln('CALM KABA - YOUR SCANNING RESULTS')
    ..writeln('Saved on ${day.format(now)} at ${time.format(now)}')
    ..writeln()
    ..writeln('This report lists the signs Calm Kaba noticed during your scans.')
    ..writeln('It is not a diagnosis. If anxiety is affecting your daily life,')
    ..writeln('please reach out to a counselor or mental health professional.')
    ..writeln();
  if (sorted.isEmpty) {
    out.writeln('No detections have been saved yet.');
    return out.toString();
  }
  out
    ..writeln('SUMMARY')
    ..writeln('From ${day.format(sorted.first.timestamp)} to ${day.format(sorted.last.timestamp)}, '
        'Calm Kaba noticed ${sorted.length} sign${sorted.length == 1 ? '' : 's'}:');
  for (final e in ranked) {
    out.writeln('  - ${e.key}: ${e.value} time${e.value == 1 ? '' : 's'}');
  }
  out
    ..writeln()
    ..writeln('WHAT THESE MEAN');
  for (final e in ranked) {
    final summary = SymptomData.data[e.key]?['summary'];
    if (summary != null) {
      out
        ..writeln('${e.key}: $summary')
        ..writeln();
    }
  }
  out.writeln('Try the Coping Techniques in the app when you notice these signs.');
  out
    ..writeln()
    ..writeln('ALL DETECTIONS');
  String? lastDay;
  for (final log in sorted) {
    final d = day.format(log.timestamp);
    if (d != lastDay) {
      out
        ..writeln()
        ..writeln(d);
      lastDay = d;
    }
    out.writeln('  ${time.format(log.timestamp).padLeft(8)}  ${log.symptom}');
  }
  return out.toString();
}
