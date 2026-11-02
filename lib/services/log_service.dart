import 'dart:io';
import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'dart:convert';

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
    final logs = await getLogs();
    final buffer = StringBuffer();
    buffer.writeln("--- Detection Logs ---");
    for (var log in logs) {
      buffer.writeln(
        '${log.timestamp.toLocal()} - Behavioral model detection: ${log.symptom}'
        '${log.score == null ? '' : ' | score ${log.score} | threshold ${log.threshold} | ${log.modelRun}'}',
      );
    }

    return saveTextFile(
      'detection_logs_${fileStamp(DateTime.now())}.txt',
      buffer.toString(),
    );
  }
}
