import 'package:flutter/foundation.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:google_generative_ai/google_generative_ai.dart';
import 'dart:convert';

class AiService {
  static const String _modelName = 'gemini-3.1-flash-lite';

  static String get _apiKey =>
      (dotenv.isInitialized ? dotenv.env['GEMINI_API_KEY'] : null) ??
      const String.fromEnvironment('GEMINI_API_KEY', defaultValue: '');

  GenerativeModel? _chatModel;
  GenerativeModel? _jsonModel;
  String? _modelKey;

  /// Free-text model, created once per API key instead of on every call.
  GenerativeModel get _model {
    _syncModels();
    return _chatModel!;
  }

  /// Model that is asked to answer with a JSON document only.
  GenerativeModel get _structuredModel {
    _syncModels();
    return _jsonModel!;
  }

  void _syncModels() {
    final key = _apiKey;
    if (key.isEmpty) {
      throw Exception('GEMINI_API_KEY is not set. Add it to the .env file.');
    }
    if (_modelKey == key && _chatModel != null && _jsonModel != null) return;
    _modelKey = key;
    _chatModel = GenerativeModel(model: _modelName, apiKey: key);
    _jsonModel = GenerativeModel(
      model: _modelName,
      apiKey: key,
      generationConfig: GenerationConfig(responseMimeType: 'application/json'),
    );
  }

  Future<Map<String, dynamic>> analyzeArtifact(
    Uint8List imageBytes, {
    String mimeType = 'image/jpeg',
  }) async {
    const prompt = '''
Analyze this archaeological artifact from the image.
Provide the analysis in VALID JSON format with exactly these keys:
- name: (A descriptive name in English)
- type: (Ceramics, Weapon, Jewelry, Coin, Tool, etc. in English)
- material: (Clay, Bronze, Gold, Stone, etc. in English)
- era: (The estimated historical period or century in English, e.g., "6th Century BC")
- purpose: (What it was used for, brief in English)
- condition: (Good, Fair, Poor, or Critical based on visible damage)
- crack_percentage: (Estimate the percentage of visible surface cracks as a number between 0 and 100)

Return ONLY the JSON object.
''';

    try {
      final content = [
        Content.multi([
          TextPart(prompt),
          DataPart(mimeType, imageBytes),
        ])
      ];

      final response = await _structuredModel.generateContent(content);
      return normalizeAnalysis(parseJsonObject(response.text));
    } catch (e) {
      debugPrint('AI Analysis Error: $e');
      throw Exception('Failed to analyze artifact: $e');
    }
  }

  /// Extracts the JSON object from a model answer. Tolerates markdown fences
  /// and prose around the object. Throws [FormatException] when there is none.
  @visibleForTesting
  static Map<String, dynamic> parseJsonObject(String? text) {
    final source = (text ?? '').trim();
    final start = source.indexOf('{');
    final end = source.lastIndexOf('}');
    if (start < 0 || end <= start) {
      throw const FormatException('The AI answer did not contain a JSON object.');
    }
    final decoded = jsonDecode(source.substring(start, end + 1));
    if (decoded is! Map) {
      throw const FormatException('The AI answer was not a JSON object.');
    }
    return Map<String, dynamic>.from(decoded);
  }

  /// Keeps only the columns of the `artifacts` table that the analysis fills,
  /// with safe types: extra keys would make the insert fail, and a value such
  /// as "12%" in `crack_percentage` would break parsing of the stored row.
  @visibleForTesting
  static Map<String, dynamic> normalizeAnalysis(Map<String, dynamic> raw) {
    String text(String key, String fallback) {
      final value = raw[key];
      final cleaned = value == null ? '' : value.toString().trim();
      return cleaned.isEmpty ? fallback : cleaned;
    }

    double crackPercentage() {
      final value = raw['crack_percentage'];
      final parsed = value is num
          ? value.toDouble()
          : double.tryParse(
              value.toString().replaceAll('%', '').replaceAll(',', '.').trim());
      if (parsed == null || parsed.isNaN || parsed.isInfinite) return 0.0;
      return parsed.clamp(0.0, 100.0).toDouble();
    }

    return {
      'name': text('name', 'Unnamed artifact'),
      'type': text('type', 'Unknown'),
      'material': text('material', 'Unknown'),
      'era': text('era', 'Unknown'),
      'purpose': text('purpose', 'Unknown'),
      'condition': text('condition', 'Stable'),
      'crack_percentage': crackPercentage(),
    };
  }

  Future<String> getComprehensiveInsight({
    required String targetMetric,
    required Map<String, dynamic> allMetricsSummary,
  }) async {
    final prompt = '''
You are the ArchoAI Senior Conservator. You have access to the museum's environmental database.
Analyze the following environmental summary for the last 24 hours:

TARGET ANALYSIS: $targetMetric

FULL SENSOR CONTEXT:
${allMetricsSummary.entries.map((e) => '- ${e.key}: ${e.value}').join('\n')}

Archaeological Safety Ratios:
- Ceramics/Wood: Needs 18-22°C and 40-50% RH.
- Metals: Needs <35% RH to prevent oxidation.
- Organic (Paper/Leather): Needs strict 50% RH, any fluctuation is dangerous.

Instruction:
1. Provide a professional assessment.
2. Focus on $targetMetric, but correlate it with other metrics (e.g., "if temp rose, explain how it affected RH").
3. Identify any "Invisible Threats" based on the data.
4. Keep it concise (3-4 sentences total). Do not use lists.
''';

    try {
      final content = [Content.text(prompt)];
      final response = await _model.generateContent(content);
      return response.text ?? 'Analysis unavailable.';
    } catch (e) {
      return 'AI Error: ${e.toString()}';
    }
  }

  Future<String> getGlobalAnalysis({
    required Map<String, dynamic> environmentSummary,
    required List<Map<String, dynamic>> artifactsSummary,
  }) async {
    final prompt = '''
You are the ArchoAI Museum Director Assistant. You have full access to the collection database and sensor network.

ENVIRONMENTAL STATUS (24H):
${environmentSummary.entries.map((e) => '- ${e.key}: ${e.value}').join('\n')}

COLLECTION STATUS:
${artifactsSummary.map((a) => '- ${a['name']} (${a['type']}): Condition ${a['condition']}, Cracks ${a['crack_percentage']}%').join('\n')}

Instruction:
Provide a high-level strategic overview of the museum's current state.
Highlight any correlations between climate and artifact degradation.
Suggest priority actions for the conservation team.
Be professional, analytical, and concise.
''';

    try {
      final content = [Content.text(prompt)];
      final response = await _model.generateContent(content);
      return response.text ?? 'Global analysis unavailable.';
    } catch (e) {
      return 'AI Error: ${e.toString()}';
    }
  }

  Future<String> askAiQuestion(String question, String context) async {
    final prompt = 'Context: $context\n\nQuestion: $question\n\nProvide professional archaeological advice based on the context above. Be concise and helpful.';
    try {
      final content = [Content.text(prompt)];
      final response = await _model.generateContent(content);
      return response.text ?? 'I could not process that question.';
    } catch (e) {
      return 'Error connecting to AI advisor: ${e.toString()}';
    }
  }
}
