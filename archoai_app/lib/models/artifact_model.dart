class ArtifactModel {
  final String id;
  final String name;
  final String imagePath;
  final String? modelUrl;
  final String? localModelPath;
  final DateTime createdAt;
  final String status;
  
  // New archival fields
  final String type;
  final String material;
  final String era;
  final String purpose;
  final String condition;
  final double crackPercentage;

  ArtifactModel({
    required this.id,
    required this.name,
    required this.imagePath,
    this.modelUrl,
    this.localModelPath,
    required this.createdAt,
    this.status = 'pending',
    this.type = 'Unknown',
    this.material = 'Unknown',
    this.era = 'Unknown',
    this.purpose = 'Unknown',
    this.condition = 'Stable',
    this.crackPercentage = 0.0,
  });

  factory ArtifactModel.fromJson(Map<String, dynamic> json) {
    return ArtifactModel(
      id: json['id'] as String,
      name: json['name'] as String,
      imagePath: json['image_path'] as String? ?? json['imagePath'] as String,
      modelUrl: json['model_url'] as String? ?? json['modelUrl'] as String?,
      localModelPath: json['local_model_path'] as String? ?? json['localModelPath'] as String?,
      createdAt: DateTime.parse(json['created_at'] as String? ?? json['createdAt'] as String),
      status: json['status'] as String? ?? 'pending',
      type: json['type'] as String? ?? 'Unknown',
      material: json['material'] as String? ?? 'Unknown',
      era: json['era'] as String? ?? 'Unknown',
      purpose: json['purpose'] as String? ?? 'Unknown',
      condition: json['condition'] as String? ?? 'Stable',
      crackPercentage: (json['crack_percentage'] as num? ?? 0.0).toDouble(),
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'name': name,
      'image_path': imagePath,
      'model_url': modelUrl,
      'local_model_path': localModelPath,
      'created_at': createdAt.toIso8601String(),
      'status': status,
      'type': type,
      'material': material,
      'era': era,
      'purpose': purpose,
      'condition': condition,
      'crack_percentage': crackPercentage,
    };
  }

  ArtifactModel copyWith({
    String? id,
    String? name,
    String? imagePath,
    String? modelUrl,
    String? localModelPath,
    DateTime? createdAt,
    String? status,
    String? type,
    String? material,
    String? era,
    String? purpose,
    String? condition,
    double? crackPercentage,
  }) {
    return ArtifactModel(
      id: id ?? this.id,
      name: name ?? this.name,
      imagePath: imagePath ?? this.imagePath,
      modelUrl: modelUrl ?? this.modelUrl,
      localModelPath: localModelPath ?? this.localModelPath,
      createdAt: createdAt ?? this.createdAt,
      status: status ?? this.status,
      type: type ?? this.type,
      material: material ?? this.material,
      era: era ?? this.era,
      purpose: purpose ?? this.purpose,
      condition: condition ?? this.condition,
      crackPercentage: crackPercentage ?? this.crackPercentage,
    );
  }
}
