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

  Future<void> addLog(String symptom, {double? score, double? threshold}) {
    final write = _writes.then((_) => _addLog(symptom, score, threshold));
    _writes = write.catchError((Object _) {});
    return write;
  }

  Future<void> _addLog(String symptom, double? score, double? threshold) async {
    final prefs = await SharedPreferences.getInstance();
    final logs = await _readLogs();
    logs.add(
      LogEntry(
        symptom: symptom,
        timestamp: DateTime.now(),
        score: score,
        threshold: threshold,
        modelRun: score == null
            ? null
            : '2026-10-27_51participants_full_vs_restricted/full/vs_everything',
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
