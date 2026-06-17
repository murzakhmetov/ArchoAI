import 'dart:io';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import '../../config/app_colors.dart';
import '../../models/artifact_model.dart';
import '../../services/tripo_service.dart';
import '../../services/local_storage_service.dart';
import '../../widgets/glass_card.dart';
import '../../widgets/neon_button.dart';
import 'model_viewer_screen.dart';

class ScannerScreen extends StatefulWidget {
  const ScannerScreen({super.key});

  @override
  State<ScannerScreen> createState() => _ScannerScreenState();
}

class _ScannerScreenState extends State<ScannerScreen> {
  File? _capturedImage;
  bool _isGenerating = false;
  int _progress = 0;
  String _statusMessage = '';
  final ImagePicker _picker = ImagePicker();

  Future<void> _takePhoto() async {
    final XFile? photo = await _picker.pickImage(
      source: ImageSource.camera,
      maxWidth: 1920,
      maxHeight: 1920,
      imageQuality: 90,
    );

    if (photo != null) {
      setState(() {
        _capturedImage = File(photo.path);
        _progress = 0;
        _statusMessage = '';
      });
    }
  }

  Future<void> _pickFromGallery() async {
    final XFile? image = await _picker.pickImage(
      source: ImageSource.gallery,
      maxWidth: 1920,
      maxHeight: 1920,
      imageQuality: 90,
    );

    if (image != null) {
      setState(() {
        _capturedImage = File(image.path);
        _progress = 0;
        _statusMessage = '';
      });
    }
  }

  Future<void> _generate3DModel() async {
    if (_capturedImage == null) return;

    setState(() {
      _isGenerating = true;
      _progress = 0;
      _statusMessage = 'Uploading image...';
    });

    try {
      // Create task from image
      setState(() => _statusMessage = 'Uploading & creating task...');
      final taskId = await TripoService.instance.createImageToModelTask(_capturedImage!);

      setState(() {
        _progress = 15;
        _statusMessage = 'Processing... This may take a few minutes';
      });

      // Poll for completion
      final result = await TripoService.instance.waitForTask(
        taskId,
        onProgress: (progress) {
          if (mounted) {
            setState(() {
              _progress = 15 + (progress * 0.85).toInt();
              if (progress < 30) {
                _statusMessage = 'Analyzing artifact geometry...';
              } else if (progress < 60) {
                _statusMessage = 'Generating 3D mesh...';
              } else if (progress < 85) {
                _statusMessage = 'Applying textures...';
              } else {
                _statusMessage = 'Finalizing model...';
              }
            });
          }
        },
      );

      if (result.isComplete && result.modelUrl != null) {
        // Save artifact
        final artifactId = DateTime.now().millisecondsSinceEpoch.toString();
        final savedImagePath = await LocalStorageService.instance
            .saveImage(_capturedImage!, artifactId);

        final artifact = ArtifactModel(
          id: artifactId,
          name: 'Artifact $artifactId',
          imagePath: savedImagePath,
          modelUrl: result.modelUrl,
          createdAt: DateTime.now(),
          status: 'complete',
        );

        await LocalStorageService.instance.saveArtifact(artifact);

        if (mounted) {
          setState(() {
            _isGenerating = false;
            _progress = 100;
            _statusMessage = 'Model ready!';
          });

          // Navigate to viewer
          Navigator.of(context).push(
            MaterialPageRoute(
              builder: (_) => ModelViewerScreen(artifact: artifact),
            ),
          );
        }
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _isGenerating = false;
          _statusMessage = '';
        });
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Error: ${e.toString()}'),
            backgroundColor: AppColors.red.withValues(alpha: 0.9),
          ),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.symmetric(horizontal: 24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SizedBox(height: 24),

              // Header
              const Text(
                '3D ARTIFACT SCANNER',
                style: TextStyle(
                  color: AppColors.textSecondary,
                  fontSize: 11,
                  fontWeight: FontWeight.w600,
                  letterSpacing: 2,
                ),
              ),
              const SizedBox(height: 4),
              const Text(
                '3D Scan',
                style: TextStyle(
                  color: AppColors.textPrimary,
                  fontSize: 28,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 8),
              const Text(
                'Take a photo of an artifact to generate a 3D model using AI',
                style: TextStyle(
                  color: AppColors.textTertiary,
                  fontSize: 13,
                ),
              ),

              const SizedBox(height: 32),

              // Image capture area
              if (_capturedImage == null)
                GlassCard(
                  padding: EdgeInsets.zero,
                  child: SizedBox(
                    height: 320,
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Container(
                          width: 80,
                          height: 80,
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            color: AppColors.cyan.withValues(alpha: 0.1),
                            border: Border.all(
                              color: AppColors.cyan.withValues(alpha: 0.3),
                              width: 1,
                            ),
                          ),
                          child: const Icon(
                            Icons.camera_alt_outlined,
                            color: AppColors.cyan,
                            size: 36,
                          ),
                        ),
                        const SizedBox(height: 20),
                        const Text(
                          'CAPTURE ARTIFACT',
                          style: TextStyle(
                            color: AppColors.textSecondary,
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                            letterSpacing: 1.5,
                          ),
                        ),
                        const SizedBox(height: 8),
                        const Text(
                          'Take a clear photo from any angle',
                          style: TextStyle(
                            color: AppColors.textTertiary,
                            fontSize: 12,
                          ),
                        ),
                        const SizedBox(height: 24),
                        Row(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            _buildCaptureButton(
                              icon: Icons.camera_alt_rounded,
                              label: 'Camera',
                              onTap: _takePhoto,
                            ),
                            const SizedBox(width: 16),
                            _buildCaptureButton(
                              icon: Icons.photo_library_outlined,
                              label: 'Gallery',
                              onTap: _pickFromGallery,
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                )
              else
                Column(
                  children: [
                    // Preview
                    GlassCard(
                      padding: const EdgeInsets.all(8),
                      child: Column(
                        children: [
                          ClipRRect(
                            borderRadius: BorderRadius.circular(12),
                            child: Image.file(
                              _capturedImage!,
                              height: 280,
                              width: double.infinity,
                              fit: BoxFit.cover,
                            ),
                          ),
                          const SizedBox(height: 12),
                          Row(
                            children: [
                              Expanded(
                                child: Text(
                                  'CAPTURED IMAGE',
                                  style: TextStyle(
                                    color: AppColors.textSecondary,
                                    fontSize: 10,
                                    fontWeight: FontWeight.w600,
                                    letterSpacing: 1.5,
                                  ),
                                ),
                              ),
                              if (!_isGenerating)
                                GestureDetector(
                                  onTap: () {
                                    setState(() => _capturedImage = null);
                                  },
                                  child: const Text(
                                    'RETAKE',
                                    style: TextStyle(
                                      color: AppColors.cyan,
                                      fontSize: 11,
                                      fontWeight: FontWeight.w600,
                                      letterSpacing: 1,
                                    ),
                                  ),
                                ),
                            ],
                          ),
                        ],
                      ),
                    ),

                    const SizedBox(height: 20),

                    // Progress
                    if (_isGenerating) ...[
                      GlassCard(
                        child: Column(
                          children: [
                            Row(
                              children: [
                                SizedBox(
                                  width: 24,
                                  height: 24,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                    value: _progress > 0 ? _progress / 100 : null,
                                    valueColor: const AlwaysStoppedAnimation(
                                      AppColors.cyan,
                                    ),
                                    backgroundColor: AppColors.surface,
                                  ),
                                ),
                                const SizedBox(width: 12),
                                Expanded(
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        'GENERATING 3D MODEL',
                                        style: TextStyle(
                                          color: AppColors.textSecondary,
                                          fontSize: 10,
                                          fontWeight: FontWeight.w600,
                                          letterSpacing: 1.5,
                                        ),
                                      ),
                                      const SizedBox(height: 4),
                                      Text(
                                        _statusMessage,
                                        style: const TextStyle(
                                          color: AppColors.textPrimary,
                                          fontSize: 13,
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                                Text(
                                  '$_progress%',
                                  style: const TextStyle(
                                    color: AppColors.cyan,
                                    fontSize: 16,
                                    fontWeight: FontWeight.w700,
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(height: 12),
                            ClipRRect(
                              borderRadius: BorderRadius.circular(2),
                              child: LinearProgressIndicator(
                                value: _progress / 100,
                                backgroundColor: AppColors.surface,
                                valueColor: const AlwaysStoppedAnimation(
                                  AppColors.cyan,
                                ),
                                minHeight: 3,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ] else ...[
                      NeonButton(
                        label: 'GENERATE 3D MODEL',
                        icon: Icons.view_in_ar_rounded,
                        onPressed: _generate3DModel,
                      ),
                    ],
                  ],
                ),

              const SizedBox(height: 32),

              // Tips
              if (_capturedImage == null)
                GlassCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        'TIPS FOR BEST RESULTS',
                        style: TextStyle(
                          color: AppColors.textSecondary,
                          fontSize: 11,
                          fontWeight: FontWeight.w600,
                          letterSpacing: 1.5,
                        ),
                      ),
                      const SizedBox(height: 16),
                      _buildTip(Icons.light_mode_outlined, 'Good lighting',
                          'Ensure even lighting without harsh shadows'),
                      const SizedBox(height: 12),
                      _buildTip(Icons.center_focus_strong_outlined, 'Clear focus',
                          'Keep the artifact centered and in sharp focus'),
                      const SizedBox(height: 12),
                      _buildTip(Icons.contrast_outlined, 'Contrast background',
                          'Use a plain background for cleaner results'),
                      const SizedBox(height: 12),
                      _buildTip(Icons.crop_free_rounded, 'Full object',
                          'Capture the entire artifact in frame'),
                    ],
                  ),
                ),

              const SizedBox(height: 100),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildCaptureButton({
    required IconData icon,
    required String label,
    required VoidCallback onTap,
  }) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
        decoration: BoxDecoration(
          color: AppColors.cyan.withValues(alpha: 0.1),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: AppColors.cyan.withValues(alpha: 0.3),
            width: 0.5,
          ),
        ),
        child: Row(
          children: [
            Icon(icon, color: AppColors.cyan, size: 20),
            const SizedBox(width: 8),
            Text(
              label,
              style: const TextStyle(
                color: AppColors.cyan,
                fontSize: 13,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildTip(IconData icon, String title, String desc) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, color: AppColors.mint, size: 18),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                title,
                style: const TextStyle(
                  color: AppColors.textPrimary,
                  fontSize: 13,
                  fontWeight: FontWeight.w500,
                ),
              ),
              const SizedBox(height: 2),
              Text(
                desc,
                style: const TextStyle(
                  color: AppColors.textTertiary,
                  fontSize: 11,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
