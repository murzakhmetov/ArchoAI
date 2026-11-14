import 'package:archoai_app/models/artifact_model.dart';
import 'package:archoai_app/services/ai_service.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('AiService.parseJsonObject', () {
    test('parses a plain JSON object', () {
      expect(AiService.parseJsonObject('{"name": "Vase"}'), {'name': 'Vase'});
    });

    test('strips markdown fences', () {
      const text = '```json\n{"name": "Vase", "crack_percentage": 4.5}\n```';
      expect(AiService.parseJsonObject(text), {
        'name': 'Vase',
        'crack_percentage': 4.5,
      });
    });

    test('tolerates prose before and after the object', () {
      const text = 'Sure! Here is the analysis:\n{"name": "Coin"}\nHope it helps.';
      expect(AiService.parseJsonObject(text), {'name': 'Coin'});
    });

    test('keeps nested braces intact', () {
      expect(
        AiService.parseJsonObject('{"a": {"b": 1}, "c": "x"}'),
        {'a': {'b': 1}, 'c': 'x'},
      );
    });

    test('throws FormatException when there is no object', () {
      expect(() => AiService.parseJsonObject(null), throwsFormatException);
      expect(() => AiService.parseJsonObject(''), throwsFormatException);
      expect(() => AiService.parseJsonObject('no json here'),
          throwsFormatException);
      expect(() => AiService.parseJsonObject('[1, 2, 3]'), throwsFormatException);
    });
  });

  group('AiService.normalizeAnalysis', () {
    test('keeps only the artifact columns and trims strings', () {
      final result = AiService.normalizeAnalysis({
        'name': '  Bronze spear  ',
        'type': 'Weapon',
        'material': 'Bronze',
        'era': '8th Century BC',
        'purpose': 'Combat',
        'condition': 'Fair',
        'crack_percentage': 12.7,
        'notes': 'not a column',
      });

      expect(result.keys, [
        'name',
        'type',
        'material',
        'era',
        'purpose',
        'condition',
        'crack_percentage',
      ]);
      expect(result['name'], 'Bronze spear');
      expect(result['crack_percentage'], 12.7);
    });

    test('fills missing or empty fields with defaults', () {
      final result = AiService.normalizeAnalysis({'name': '   ', 'type': null});

      expect(result['name'], 'Unnamed artifact');
      expect(result['type'], 'Unknown');
      expect(result['condition'], 'Stable');
      expect(result['crack_percentage'], 0.0);
    });

    test('coerces crack_percentage from strings and clamps to 0..100', () {
      double crack(Object? value) =>
          AiService.normalizeAnalysis({'crack_percentage': value})
              ['crack_percentage'] as double;

      expect(crack('12.5%'), 12.5);
      expect(crack('7,5'), 7.5);
      expect(crack(150), 100.0);
      expect(crack(-5), 0.0);
      expect(crack('abc'), 0.0);
      expect(crack(null), 0.0);
      expect(crack(double.nan), 0.0);
      expect(crack(double.infinity), 0.0);
    });

    test('output is accepted by ArtifactModel.fromJson', () {
      final json = AiService.normalizeAnalysis({
        'name': 'Vessel',
        'crack_percentage': '3%',
      });

      final artifact = ArtifactModel.fromJson({
        ...json,
        'id': 'abc',
        'image_path': '',
        'created_at': '2026-11-13T10:00:00Z',
      });

      expect(artifact.name, 'Vessel');
      expect(artifact.crackPercentage, 3.0);
    });
  });
}
