import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:thesis_anxiety/homepage.dart';
import 'package:thesis_anxiety/coping_techniques/cognitive_reframing.dart';
import 'package:thesis_anxiety/detection_instant.dart';
import 'package:thesis_anxiety/services/detection_service.dart';
import 'package:thesis_anxiety/services/log_service.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:thesis_anxiety/theme/design_system.dart';

BehaviorResult result(double? score) => BehaviorResult.fromMap({
  'symptom': 'fidgeting',
  'score': score,
  'threshold': .5,
});

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test(
    'sustained detection logs once and missing tracking cannot rearm it',
    () {
      final rule = DetectionEventRule();
      expect(rule.accept(result(.8), 5), isTrue);
      for (var t = 7; t < 100; t += 2) {
        expect(rule.accept(result(.8), t.toDouble()), isFalse);
      }
      expect(rule.accept(result(null), 101), isFalse);
      expect(rule.accept(result(null), 103), isFalse);
      expect(rule.accept(result(.9), 105), isFalse);
      expect(rule.accept(result(.1), 107), isFalse);
      expect(rule.accept(result(.2), 109), isFalse);
      expect(rule.accept(result(.9), 111), isTrue);
      expect(rule.accept(result(.1), 113), isFalse);
      expect(rule.accept(result(.1), 115), isFalse);
      expect(rule.accept(result(.9), 117), isFalse);
    },
  );
  test('decisions average consecutive windows and restart on a gap', () {
    final smoother = ScoreSmoother();
    double? score(double? raw, double end) =>
        smoother.apply([result(raw)], end).single.score;
    // smooth3 from the first window: no wait, then at most three windows.
    expect(score(.9, 5), closeTo(.9, 1e-9));
    expect(score(.3, 7), closeTo(.6, 1e-9));
    expect(score(.6, 9), closeTo(.6, 1e-9));
    expect(score(0, 11), closeTo(.3, 1e-9));
    expect(score(.9, 20), closeTo(.9, 1e-9));
    expect(score(null, 22), isNull);
    expect(score(.4, 24), closeTo(.4, 1e-9));
  });
  test(
    'simultaneous detections persist without overwriting each other',
    () async {
      SharedPreferences.setMockInitialValues({});
      final logs = LogService();
      await Future.wait([
        logs.addLog('Fidgeting', score: .8, threshold: .5, modelRun: 'run'),
        logs.addLog('Hand Tremor', score: .6, threshold: .5),
      ]);
      final saved = await logs.getLogs();
      expect(saved.map((e) => e.symptom), ['Fidgeting', 'Hand Tremor']);
      expect(saved.first.score, .8);
      expect(saved.first.modelRun, 'run');
      expect(
        LogEntry.fromMap({
          'symptom': 'Old log',
          'timestamp': '2025-01-01T00:00:00',
        }).score,
        isNull,
      );
    },
  );
  test(
    'instant waits for a valid threshold crossing and ignores later packets',
    () async {
      final messenger =
          TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
      var stopCalls = 0;
      messenger.setMockMethodCallHandler(
        const MethodChannel('calm/detection'),
        (call) async {
          if (call.method == 'stop') stopCalls++;
          return null;
        },
      );
      messenger.setMockMethodCallHandler(
        const MethodChannel('calm/detection/events'),
        (_) async => null,
      );
      final service = DetectionService();
      final detected = <BehaviorResult>[];
      final subscription = service.detectionStream.listen(detected.add);
      var end = 3.0;
      Future<void> packet(double? score) async {
        end += 2;
        await messenger.handlePlatformMessage(
          'calm/detection/events',
          StandardMethodCodec().encodeSuccessEnvelope({
            'type': 'prediction',
            'end': end,
            'results': [
              {'symptom': 'fidgeting', 'score': score, 'threshold': .5},
            ],
          }),
          (_) {},
        );
        await Future<void>.delayed(Duration.zero);
      }

      await service.startDetection();
      await packet(null);
      await packet(.49);
      expect(detected, isEmpty);
      await packet(.48);
      expect(detected, isEmpty);
      await packet(.53);
      expect(detected.single.label, 'Fidgeting');
      // smooth3: mean(.49, .48, .53) reaches the .5 threshold.
      expect(detected.single.score, closeTo(.5, 1e-9));
      expect(stopCalls, 1);
      await packet(.9);
      expect(detected.length, 1);
      await subscription.cancel();
      await service.dispose();
      messenger.setMockMethodCallHandler(
        const MethodChannel('calm/detection'),
        null,
      );
      messenger.setMockMethodCallHandler(
        const MethodChannel('calm/detection/events'),
        null,
      );
    },
  );
  testWidgets('instant result fits a small phone with model details', (
    tester,
  ) async {
    GoogleFonts.config.allowRuntimeFetching = false;
    await tester.binding.setSurfaceSize(const Size(360, 640));
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.lightTheme,
        onGenerateRoute: (_) => MaterialPageRoute<void>(
          settings: const RouteSettings(
            arguments: {
              'symptom': 'Heavy Breathing',
              'score': .7,
              'threshold': .5,
            },
          ),
          builder: (_) => const DetectionInstantPage(),
        ),
      ),
    );
    expect(find.text('Heavy Breathing'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    await tester.binding.setSurfaceSize(null);
  });
  testWidgets('home has real camera status and scan mode controls', (
    tester,
  ) async {
    GoogleFonts.config.allowRuntimeFetching = false;
    debugDefaultTargetPlatformOverride = TargetPlatform.windows;
    await tester.binding.setSurfaceSize(const Size(430, 1100));
    await tester.pumpWidget(
      MaterialApp(theme: AppTheme.lightTheme, home: const HomePage()),
    );
    expect(find.text('Camera detection requires Android'), findsOneWidget);
    expect(find.text('Scan Now'), findsOneWidget);
    expect(find.textContaining('Video is not saved.'), findsOneWidget);
    await tester.tap(find.text('Continuous Scan').first);
    await tester.pump();
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    await tester.pump();
    await tester.binding.setSurfaceSize(null);
    debugDefaultTargetPlatformOverride = null;
  });

  testWidgets('kept reflection is listed under past reflections', (
    tester,
  ) async {
    GoogleFonts.config.allowRuntimeFetching = false;
    SharedPreferences.setMockInitialValues({});
    const channel = MethodChannel('calm/detection');
    final messenger = tester.binding.defaultBinaryMessenger;
    messenger.setMockMethodCallHandler(
      channel,
      (call) async => '/Download/Calm Kaba/${call.arguments['name']}',
    );
    await tester.binding.setSurfaceSize(const Size(430, 1600));
    Future<void> open() => tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.lightTheme,
        home: const CognitiveReframingPage(),
      ),
    );

    await open();
    await tester.pumpAndSettle();
    expect(find.text('Past reflections'), findsNothing);
    await tester.enterText(find.byType(TextField).at(0), 'I will fail');
    await tester.enterText(find.byType(TextField).at(3), 'I prepared well');
    await tester.tap(find.text('Finish'));
    await tester.pumpAndSettle();
    expect(find.text('Keep this reflection?'), findsOneWidget);
    await tester.tap(find.text('Keep it'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Kept only on this phone'), findsOneWidget);

    await tester.pumpWidget(const SizedBox());
    await open();
    await tester.pumpAndSettle();
    expect(find.text('Past reflections'), findsOneWidget);
    // The balanced thought leads, not the worry.
    expect(find.text('I prepared well'), findsOneWidget);
    await tester.tap(find.text('I prepared well'));
    await tester.pumpAndSettle();
    expect(find.text('I will fail'), findsOneWidget);

    await tester.tap(find.text('Edit'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).at(3), 'I am ready');
    await tester.tap(find.text('Save changes'));
    await tester.pumpAndSettle();
    expect((await Reflections.load()).single.answers.last, 'I am ready');

    await tester.pumpWidget(const SizedBox());
    await open();
    await tester.pumpAndSettle();
    await tester.tap(find.text('I am ready'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Delete'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Delete')); // Confirm.
    await tester.pumpAndSettle();
    expect(find.text('Past reflections'), findsNothing);

    messenger.setMockMethodCallHandler(channel, null);
    await tester.binding.setSurfaceSize(null);
  });
}
