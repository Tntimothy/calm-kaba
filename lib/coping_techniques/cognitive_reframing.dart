import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../services/log_service.dart';
import '../theme/design_system.dart';

const _prompts = [
  ('1. Identify the negative thought', 'What is bothering you right now?'),
  ('2. Examine the evidence', 'What facts support or contradict this thought?'),
  (
    '3. Spot Cognitive distortions',
    'Are you mind-reading, catastrophizing, or using "all-or-nothing" thinking?',
  ),
  (
    '4. Replace with a balanced alternative',
    'What is a more realistic, compassionate way to look at this?',
  ),
];

class CognitiveReframingPage extends StatefulWidget {
  const CognitiveReframingPage({super.key});

  @override
  State<CognitiveReframingPage> createState() => _CognitiveReframingPageState();
}

class _CognitiveReframingPageState extends State<CognitiveReframingPage> {
  bool _isSubmitted = false;
  String _doneMessage = '';
  List<Reflection> _past = [];
  bool _showAllPast = false;
  DateTime? _editing; // Time of the kept entry loaded into the fields.
  final _scroll = ScrollController();
  final _answers = List.generate(
    _prompts.length,
    (_) => TextEditingController(),
  );

  @override
  void initState() {
    super.initState();
    _loadPast();
  }

  @override
  void dispose() {
    for (final c in _answers) {
      c.dispose();
    }
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _loadPast() async {
    final past = await Reflections.load();
    if (mounted) setState(() => _past = past);
  }

  void _startEdit(Reflection r) {
    for (var i = 0; i < _answers.length; i++) {
      _answers[i].text = r.answers[i];
    }
    setState(() => _editing = r.time);
    _scroll.animateTo(
      0,
      duration: const Duration(milliseconds: 300),
      curve: Curves.easeOut,
    );
  }

  void _cancelEdit() {
    for (final c in _answers) {
      c.clear();
    }
    setState(() => _editing = null);
  }

  Future<void> _delete(Reflection r) async {
    final sure = await _askChoice(
      'Delete this reflection?',
      'It will be removed from this page. The text file in '
          'Download/Calm Kaba stays unless you delete it there.',
      'Cancel',
      'Delete',
    );
    if (sure != true) return;
    await Reflections.replace(r.time, null);
    if (_editing == r.time) _cancelEdit();
    await _loadPast();
  }

  /// Asks whether to keep the entry; kept entries go to this phone only.
  Future<void> _submit() async {
    final answers = [for (final c in _answers) c.text.trim()];
    final written = answers.any((a) => a.isNotEmpty);
    var message = 'Nothing was written, so nothing was saved.';
    final editing = _editing;
    if (editing != null) {
      // Already kept, so no prompt. The .txt copy keeps the original text.
      if (written) await Reflections.replace(editing, answers);
      message = written
          ? 'Your changes were kept on this phone.'
          : 'Nothing was written, so nothing was changed.';
    } else if (written) {
      final keep = await _askChoice(
        'Keep this reflection?',
        'You can look back on it later at the bottom of this page.',
        'Let it go',
        'Keep it',
      );
      if (keep == null) return; // Dismissed: keep writing.
      if (!keep) {
        message = 'You let this one go. Nothing was saved.';
      } else {
        final now = DateTime.now();
        final text = StringBuffer('Reframing Journal - $now\n');
        for (var i = 0; i < _prompts.length; i++) {
          text.writeln('\n${_prompts[i].$1}\n${answers[i]}');
        }
        try {
          final path = await saveTextFile(
            'reframing_${fileStamp(now)}.txt',
            text.toString(),
          );
          await Reflections.add(now, answers);
          message =
              'Kept only on this phone. Find it under Past reflections, '
              'or as a text file: $path';
        } catch (e) {
          if (mounted) {
            ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(content: Text('Could not keep your entry: $e')),
            );
          }
          return;
        }
      }
    }
    if (mounted) {
      setState(() {
        _doneMessage = message;
        _isSubmitted = true;
      });
    }
  }

  /// The past entry's balanced thought if they wrote one, else what they wrote first.
  static String _headline(Reflection r) => r.answers.last.isNotEmpty
      ? r.answers.last
      : r.answers.firstWhere((a) => a.isNotEmpty, orElse: () => '');

  Widget _buildPastReflections(bool isLightMode) {
    if (_past.isEmpty) return const SizedBox.shrink();
    final shown = _showAllPast ? _past : _past.take(3);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              'Past reflections',
              style: AppTypography.titleLarge.copyWith(
                color: AppColors.primary,
              ),
            ),
            if (_past.length > 3)
              TextButton(
                onPressed: () => setState(() => _showAllPast = !_showAllPast),
                child: Text(
                  _showAllPast ? 'Show less' : 'View all',
                  style: AppTypography.bodyMedium.copyWith(
                    color: AppColors.primary,
                  ),
                ),
              ),
          ],
        ),
        const SizedBox(height: 12),
        for (final r in shown)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Material(
              color: isLightMode ? Colors.white : AppColors.pastelPetal,
              borderRadius: BorderRadius.circular(16),
              child: InkWell(
                borderRadius: BorderRadius.circular(16),
                onTap: () => _showReflection(r),
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Row(
                    children: [
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 10,
                          vertical: 8,
                        ),
                        decoration: BoxDecoration(
                          color: isLightMode
                              ? AppColors.pastelPetal
                              : Colors.white,
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: Text(
                          DateFormat('d MMM').format(r.time).toUpperCase(),
                          style: AppTypography.bodySmall.copyWith(
                            color: AppColors.primary,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                      ),
                      const SizedBox(width: 16),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              _headline(r),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: AppTypography.titleMedium.copyWith(
                                color: AppColors.textPrimary,
                              ),
                            ),
                            Text(
                              DateFormat('h:mm a').format(r.time),
                              style: AppTypography.bodySmall.copyWith(
                                color: AppColors.textSecondary,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }

  void _showReflection(Reflection r) {
    showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        backgroundColor: Theme.of(context).brightness == Brightness.light
            ? Theme.of(
                context,
              ).extension<AppColorsExtension>()!.secondaryBackground
            : Colors.white,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(24)),
        title: Text(
          DateFormat('MMM d, y · h:mm a').format(r.time),
          style: AppTypography.titleLarge.copyWith(color: AppColors.primary),
        ),
        content: SingleChildScrollView(
          child: Column(
            children: [
              for (var i = 0; i < _prompts.length; i++)
                if (r.answers[i].isNotEmpty)
                  Container(
                    width: double.infinity,
                    margin: const EdgeInsets.only(bottom: 12),
                    padding: const EdgeInsets.all(16),
                    decoration: BoxDecoration(
                      color: AppColors.pastelPetal.withValues(alpha: .5),
                      borderRadius: BorderRadius.circular(16),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          _prompts[i].$1,
                          style: AppTypography.titleMedium.copyWith(
                            color: AppColors.primary,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          r.answers[i],
                          style: AppTypography.bodyMedium.copyWith(
                            color: AppColors.textPrimary,
                          ),
                        ),
                      ],
                    ),
                  ),
            ],
          ),
        ),
        actions: [
          for (final (label, color, onTap) in [
            ('Delete', AppColors.pinkCarnation, () => _delete(r)),
            ('Edit', AppColors.primary, () => _startEdit(r)),
            ('Close', AppColors.primary, () {}),
          ])
            TextButton(
              onPressed: () {
                Navigator.of(context).pop();
                onTap();
              },
              child: Text(
                label,
                style: AppTypography.titleMedium.copyWith(color: color),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildJournalField(
    String title,
    String hint,
    TextEditingController controller,
    bool isLightMode,
  ) {
    return Container(
      margin: const EdgeInsets.only(bottom: 24),
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(isLightMode ? 12 : 24),
        border: isLightMode
            ? null
            : Border.all(color: AppColors.elizabeth, width: 4),
        boxShadow: isLightMode
            ? [
                BoxShadow(
                  color: Colors.black.withValues(alpha: 0.12),
                  blurRadius: 8,
                  offset: const Offset(0, 3),
                ),
              ]
            : [],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: AppTypography.titleLarge.copyWith(color: AppColors.primary),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: controller,
            maxLines: 4,
            style: AppTypography.bodyLarge.copyWith(color: AppColors.primary),
            decoration: InputDecoration(
              hintText: hint,
              hintStyle: AppTypography.bodyMedium.copyWith(
                color: AppColors.primary.withValues(alpha: 0.5),
              ),
              filled: true,
              fillColor: isLightMode
                  ? const Color(0xFFF0F0F0)
                  : AppColors.pastelPetal,
              contentPadding: const EdgeInsets.all(16),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(16),
                borderSide: BorderSide.none,
              ),
              enabledBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(16),
                borderSide: BorderSide.none,
              ),
              focusedBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(16),
                borderSide: const BorderSide(
                  color: AppColors.primary,
                  width: 2,
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildCompletionScreen(bool isLightMode) {
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(32),
        child: Container(
          padding: const EdgeInsets.all(32),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(isLightMode ? 12 : 24),
            border: isLightMode
                ? null
                : Border.all(color: AppColors.elizabeth, width: 4),
            boxShadow: isLightMode
                ? [
                    BoxShadow(
                      color: Colors.black.withValues(alpha: 0.12),
                      blurRadius: 8,
                      offset: const Offset(0, 3),
                    ),
                  ]
                : [],
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Container(
                padding: const EdgeInsets.all(16),
                decoration: const BoxDecoration(
                  color: AppColors.teaGreen,
                  shape: BoxShape.circle,
                ),
                child: const Icon(
                  Icons.self_improvement,
                  size: 80,
                  color: AppColors.primary,
                ),
              ),
              const SizedBox(height: 24),
              Text(
                'Well Done',
                style: AppTypography.headlineMedium.copyWith(
                  color: AppColors.primary,
                ),
              ),
              const SizedBox(height: 16),
              Text(
                'You took a great step in challenging negative thoughts. '
                '$_doneMessage',
                style: AppTypography.bodyMedium.copyWith(
                  color: AppColors.textPrimary,
                ),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 40),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: () => Navigator.pop(context),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppColors.primary,
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(30),
                    ),
                    padding: const EdgeInsets.symmetric(vertical: 16),
                  ),
                  child: Text(
                    'Done',
                    style: AppTypography.titleMedium.copyWith(
                      color: Colors.white,
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  /// Two-button dialog; null when dismissed.
  Future<bool?> _askChoice(String title, String body, String no, String yes) {
    return showDialog<bool>(
      context: context,
      builder: (context) {
        final isLightMode = Theme.of(context).brightness == Brightness.light;
        return AlertDialog(
          backgroundColor: isLightMode
              ? Theme.of(
                  context,
                ).extension<AppColorsExtension>()!.secondaryBackground
              : Colors.white,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(24),
          ),
          title: Text(
            title,
            style: AppTypography.titleLarge.copyWith(color: AppColors.primary),
          ),
          content: Text(
            body,
            style: AppTypography.bodyMedium.copyWith(
              color: isLightMode ? AppColors.textPrimary : AppColors.secondary,
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(false),
              child: Text(
                no,
                style: AppTypography.titleMedium.copyWith(
                  color: AppColors.primary,
                ),
              ),
            ),
            TextButton(
              onPressed: () => Navigator.of(context).pop(true),
              child: Text(
                yes,
                style: AppTypography.titleMedium.copyWith(
                  color: AppColors.pinkCarnation,
                ),
              ),
            ),
          ],
        );
      },
    );
  }

  Future<bool> _onWillPop() async {
    // If they have already finished let them exit.
    if (_isSubmitted) {
      return true;
    }
    return await _askChoice(
          'Exit Session?',
          'Are you sure you want to exit before completing Cognitive Reframing?',
          'Cancel',
          'Exit',
        ) ??
        false;
  }

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<ThemeMode>(
      valueListenable: AppTheme.themeModeNotifier,
      builder: (context, themeMode, child) {
        final bool isLightMode = themeMode == ThemeMode.light;

        return PopScope(
          canPop: false,
          onPopInvokedWithResult: (didPop, result) async {
            if (didPop) return;
            final shouldPop = await _onWillPop();
            if (shouldPop && context.mounted) {
              Navigator.of(context).pop();
            }
          },
          child: Scaffold(
            backgroundColor: Theme.of(context).scaffoldBackgroundColor,
            body: SafeArea(
              child: _isSubmitted
                  ? _buildCompletionScreen(isLightMode)
                  : SingleChildScrollView(
                      controller: _scroll,
                      padding: const EdgeInsets.all(24),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Align(
                            alignment: Alignment.centerLeft,
                            child: IconButton(
                              onPressed: () async {
                                final shouldPop = await _onWillPop();
                                if (shouldPop && context.mounted) {
                                  Navigator.of(context).pop();
                                }
                              },
                              icon: const Icon(Icons.arrow_back_ios_new),
                              color: AppColors.primary,
                              tooltip: 'Back',
                            ),
                          ),
                          const SizedBox(height: 8),
                          Text(
                            'Reframing Journal',
                            style: AppTypography.headlineMedium.copyWith(
                              color: AppColors.primary,
                            ),
                          ),
                          const SizedBox(height: 8),
                          Text(
                            'Work through your thoughts by filling out '
                            'the journal prompts below.',
                            style: AppTypography.bodyMedium.copyWith(
                              color: AppColors.primary,
                            ),
                          ),
                          if (_editing != null)
                            Row(
                              children: [
                                Expanded(
                                  child: Text(
                                    'Editing your reflection from '
                                    '${DateFormat('MMM d, h:mm a').format(_editing!)}',
                                    style: AppTypography.bodyMedium.copyWith(
                                      color: AppColors.primary,
                                      fontWeight: FontWeight.w700,
                                    ),
                                  ),
                                ),
                                TextButton(
                                  onPressed: _cancelEdit,
                                  child: Text(
                                    'Cancel',
                                    style: AppTypography.bodyMedium.copyWith(
                                      color: AppColors.primary,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                          const SizedBox(height: 24),
                          for (var i = 0; i < _prompts.length; i++)
                            _buildJournalField(
                              _prompts[i].$1,
                              _prompts[i].$2,
                              _answers[i],
                              isLightMode,
                            ),
                          const SizedBox(height: 16),
                          SizedBox(
                            width: double.infinity,
                            child: ElevatedButton(
                              onPressed: _submit,
                              style: ElevatedButton.styleFrom(
                                backgroundColor: AppColors.primary,
                                shape: RoundedRectangleBorder(
                                  borderRadius: BorderRadius.circular(30),
                                ),
                                padding: const EdgeInsets.symmetric(
                                  vertical: 20,
                                ),
                              ),
                              child: Text(
                                _editing == null ? 'Finish' : 'Save changes',
                                style: AppTypography.titleLarge.copyWith(
                                  color: Colors.white,
                                ),
                              ),
                            ),
                          ),
                          const SizedBox(height: 32),
                          // Below the new entry so past thoughts never greet someone mid-worry.
                          _buildPastReflections(isLightMode),
                          const SizedBox(height: 24),
                        ],
                      ),
                    ),
            ),
          ),
        );
      },
    );
  }
}
