import 'package:flutter/foundation.dart';
import 'package:google_generative_ai/google_generative_ai.dart';
import 'dart:convert';

class AiService {
  static const String _apiKey = 'AIzaSyCXID2IengXMwvRQ-6wibg84CthQV38gn0';
  
  final _model = GenerativeModel(
    model: 'gemini-3.1-flash-lite',
    apiKey: _apiKey,
  );

  Future<Map<String, dynamic>> analyzeArtifact(Uint8List imageBytes) async {
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
          DataPart('image/jpeg', imageBytes),
        ])
      ];
      
      final response = await _model.generateContent(content);
      final text = response.text ?? '{}';
      
      // Clean up potential markdown formatting
      final cleanedJson = text.replaceAll('```json', '').replaceAll('```', '').trim();
      return jsonDecode(cleanedJson) as Map<String, dynamic>;
    } catch (e) {
      debugPrint('AI Analysis Error: $e');
      throw Exception('Failed to analyze artifact: $e');
    }
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
