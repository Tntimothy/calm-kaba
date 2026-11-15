import 'package:flutter_test/flutter_test.dart';
import 'package:thesis_anxiety/coping_techniques/breathing_regulation.dart';
import 'package:thesis_anxiety/services/log_service.dart';

void main() {
  test('report counts symptoms, explains them and groups by day', () {
    final report = buildReport([
      LogEntry(symptom: 'Fear', timestamp: DateTime(2026, 11, 15, 22, 10)),
      LogEntry(symptom: 'Hand Tremor', timestamp: DateTime(2026, 11, 14, 9, 5)),
      LogEntry(symptom: 'Fear', timestamp: DateTime(2026, 11, 15, 22, 11)),
    ], DateTime(2026, 11, 15, 23));

    expect(report, contains('noticed 3 signs'));
    expect(report, contains('- Fear: 2 times'));
    expect(report, contains('- Hand Tremor: 1 time\n'));
    expect(report, contains('Fear: Your face may be showing fear'));
    expect(
      report.indexOf('November 14, 2026\n'),
      lessThan(report.indexOf('November 15, 2026\n')),
    );
    expect(report, contains('10:10 PM  Fear'));
  });

  test('empty report says so', () {
    expect(buildReport([], DateTime(2026, 11, 15)), contains('No detections'));
  });

  test('phaseSecond counts 1-based within each phase', () {
    expect(phaseSecond(0, [4, 7, 8]), 1);
    expect(phaseSecond(3.9, [4, 7, 8]), 4);
    expect(phaseSecond(4.0, [4, 7, 8]), 1);
    expect(phaseSecond(10.5, [4, 7, 8]), 7);
    expect(phaseSecond(18.99, [4, 7, 8]), 8);
    expect(phaseSecond(19, [4, 7, 8]), 8);
  });
}
