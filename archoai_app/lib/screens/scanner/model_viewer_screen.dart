import 'dart:io';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:model_viewer_plus/model_viewer_plus.dart';
import '../../config/app_colors.dart';
import '../../models/artifact_model.dart';
import '../../services/tripo_service.dart';
import '../../services/local_storage_service.dart';
import '../../services/supabase_service.dart';
import 'package:share_plus/share_plus.dart';

class ModelViewerScreen extends StatefulWidget {
  final ArtifactModel artifact;

  const ModelViewerScreen({super.key, required this.artifact});

  @override
  State<ModelViewerScreen> createState() => _ModelViewerScreenState();
}

class _ModelViewerScreenState extends State<ModelViewerScreen> {
  bool _isDownloading = false;
  bool _isDownloaded = false;
  String? _localPath;
  String? _currentModelUrl;
  bool _isLoadingLocal = false;
  String? _base64Model;

  @override
  void initState() {
    super.initState();
    _currentModelUrl = widget.artifact.modelUrl;
    _checkLocalModel();
    debugPrint('3D MODEL VIEWER: Initializing with ${widget.artifact.name}');
    debugPrint('3D MODEL VIEWER: Network URL: ${widget.artifact.modelUrl}');
    debugPrint('3D MODEL VIEWER: Source to be used: $_modelSource');
  }

  Future<void> _checkLocalModel() async {
    final hasLocal = await LocalStorageService.instance.modelExists(widget.artifact.id);
    if (hasLocal) {
      final path = await LocalStorageService.instance.getModelSavePath(widget.artifact.id);
      if (mounted) {
        setState(() {
          _isDownloaded = true;
          _localPath = path;
        });
        _loadLocalAsBase64(path);
      }
    }
  }

  Future<void> _loadLocalAsBase64(String path) async {
    setState(() => _isLoadingLocal = true);
    try {
      final file = File(path);
      if (await file.exists()) {
        final bytes = await file.readAsBytes();
        final base64 = base64Encode(bytes);
        if (mounted) {
          setState(() {
            _base64Model = 'data:model/gltf-binary;base64,$base64';
            _isLoadingLocal = false;
          });
        }
      }
    } catch (e) {
      debugPrint('Error loading local model as base64: $e');
      if (mounted) setState(() => _isLoadingLocal = false);
    }
  }

  // Removed _repairArtifactWithUpload as per user request to avoid cloud file uploads

  Future<void> _downloadModel() async {
    if (widget.artifact.modelUrl == null) return;

    setState(() => _isDownloading = true);

    try {
      final savePath = await LocalStorageService.instance
          .getModelSavePath(widget.artifact.id);

      await TripoService.instance.downloadModel(
        widget.artifact.modelUrl!,
        savePath,
      );

      // Update artifact locally
      final updated = widget.artifact.copyWith(localModelPath: savePath);
      await LocalStorageService.instance.saveArtifact(updated);

      // Update artifact in Supabase PERMANENTLY so it persists after app restart
      // We pass the current URL as well to keep it accessible
      await SupabaseService.instance.updateArtifactModelPath(
        widget.artifact.id, 
        savePath, 
        modelUrl: _currentModelUrl
      );

      setState(() {
        _isDownloading = false;
        _isDownloaded = true;
        _localPath = savePath;
      });

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: const Text('Model saved to device'),
            backgroundColor: AppColors.mint.withValues(alpha: 0.9),
          ),
        );
      }
    } catch (e) {
      setState(() => _isDownloading = false);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Download failed: ${e.toString()}'),
            backgroundColor: AppColors.red.withValues(alpha: 0.9),
          ),
        );
      }
    }
  }

  Future<void> _shareModel() async {
    if (_localPath == null) return;
    
    final file = XFile(_localPath!);
    await Share.shareXFiles(
      [file],
      text: '3D Model of ${widget.artifact.name}',
      subject: 'ArchoAI 3D Artifact Export',
    );
  }

  String get _modelSource {
    // 1. If we have a local file already loaded as Base64, use it.
    // This is the most reliable way to show local files in a Mobile WebView.
    if (_base64Model != null) {
      return _base64Model!;
    }

    // 2. Fallback to network URL (prioritizing Supabase/Tripo)
    if (_currentModelUrl != null && _currentModelUrl!.isNotEmpty) {
      return _currentModelUrl!;
    }
    
    return '';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        backgroundColor: AppColors.background,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back_ios_new_rounded, size: 20),
          onPressed: () => Navigator.of(context).pop(),
        ),
        title: Column(
          children: [
            const Text(
              '3D MODEL VIEWER',
              style: TextStyle(
                fontSize: 10,
                fontWeight: FontWeight.w600,
                letterSpacing: 2,
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(height: 2),
            Text(
              widget.artifact.name,
              style: const TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.w600,
                color: AppColors.textPrimary,
              ),
            ),
          ],
        ),
        actions: [
          // Download button
          if (widget.artifact.modelUrl != null)
            IconButton(
              icon: _isDownloading
                  ? const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(
                        strokeWidth: 2,
                        valueColor: AlwaysStoppedAnimation(AppColors.cyan),
                      ),
                    )
                  : Icon(
                      _isDownloaded
                          ? Icons.download_done_rounded
                          : Icons.download_rounded,
                      color: _isDownloaded ? AppColors.mint : AppColors.cyan,
                    ),
              onPressed: _isDownloading || _isDownloaded
                  ? null
                  : _downloadModel,
            ),
          
          if (_isDownloaded)
            IconButton(
              icon: const Icon(Icons.share_rounded, color: AppColors.cyan),
              onPressed: _shareModel,
            ),
        ],
      ),
      body: Column(
        children: [
          // 3D Viewer
          Expanded(
            child: Container(
              margin: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: AppColors.surface,
                borderRadius: BorderRadius.circular(16),
                border: Border.all(
                  color: AppColors.cardBorder,
                  width: 1,
                ),
              ),
              child: ClipRRect(
                borderRadius: BorderRadius.circular(15),
                child: _modelSource.isNotEmpty
                    ? Stack(
                        children: [
                          ModelViewer(
                            key: ValueKey(_modelSource),
                            src: _modelSource,
                            alt: widget.artifact.name,
                            autoPlay: true,
                            autoRotate: true,
                            cameraControls: true,
                            backgroundColor: AppColors.surface,
                            shadowIntensity: 0.5,
                          ),
                          // Diagnostic overlay (hidden by default, can be seen in code)
                          /*
                          Positioned(
                            bottom: 10,
                            left: 10,
                            child: Text(
                              'Src: ${_modelSource.substring(0, _modelSource.length > 30 ? 30 : _modelSource.length)}...',
                              style: TextStyle(fontSize: 8, color: Colors.white.withOpacity(0.3)),
                            ),
                          ),
                          */
                        ],
                      )
                    : (_isRepairing 
                        ? const Center(
                            child: Column(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                CircularProgressIndicator(color: AppColors.cyan),
                                SizedBox(height: 16),
                                Text(
                                  'PREPARING 3D VIEW...',
                                  style: TextStyle(
                                    color: AppColors.textSecondary,
                                    fontSize: 10,
                                    letterSpacing: 1,
                                    fontWeight: FontWeight.w600,
                                  ),
                                ),
                              ],
                            ),
                          )
                        : const Center(
                            child: Text(
                              'Model source is missing',
                              style: TextStyle(color: AppColors.textSecondary),
                            ),
                          )),
              ),
            ),
          ),

          // Info bar
          Container(
            padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
            decoration: BoxDecoration(
              color: AppColors.surface,
              border: Border(
                top: BorderSide(color: AppColors.cardBorder, width: 0.5),
              ),
            ),
            child: Column(
              children: [
                // Controls hint
                Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    _buildControlHint(Icons.touch_app_outlined, 'Rotate'),
                    const SizedBox(width: 24),
                    _buildControlHint(Icons.pinch_outlined, 'Zoom'),
                    const SizedBox(width: 24),
                    _buildControlHint(Icons.open_with_rounded, 'Pan'),
                  ],
                ),
                const SizedBox(height: 16),

                // Download button (large)
                if (!_isDownloaded && widget.artifact.modelUrl != null)
                  SizedBox(
                    width: double.infinity,
                    height: 50,
                    child: ElevatedButton.icon(
                      onPressed: _isDownloading ? null : _downloadModel,
                      icon: _isDownloading
                          ? const SizedBox(
                              width: 18,
                              height: 18,
                              child: CircularProgressIndicator(
                                strokeWidth: 2,
                                valueColor: AlwaysStoppedAnimation(
                                  AppColors.background,
                                ),
                              ),
                            )
                          : const Icon(Icons.download_rounded, size: 20),
                      label: Text(
                        _isDownloading ? 'DOWNLOADING...' : 'SAVE TO DEVICE',
                        style: const TextStyle(
                          letterSpacing: 1,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: AppColors.cyan,
                        foregroundColor: AppColors.background,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(12),
                        ),
                      ),
                    ),
                  ),

                if (_isDownloaded)
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 16,
                      vertical: 10,
                    ),
                    decoration: BoxDecoration(
                      color: AppColors.mint.withValues(alpha: 0.1),
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(
                        color: AppColors.mint.withValues(alpha: 0.3),
                        width: 0.5,
                      ),
                    ),
                    child: const Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(
                          Icons.check_circle_outline_rounded,
                          color: AppColors.mint,
                          size: 18,
                        ),
                        SizedBox(width: 8),
                        Text(
                          'SAVED TO DEVICE',
                          style: TextStyle(
                            color: AppColors.mint,
                            fontSize: 12,
                            fontWeight: FontWeight.w700,
                            letterSpacing: 1,
                          ),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildControlHint(IconData icon, String label) {
    return Column(
      children: [
        Icon(icon, color: AppColors.textTertiary, size: 20),
        const SizedBox(height: 4),
        Text(
          label.toUpperCase(),
          style: const TextStyle(
            color: AppColors.textTertiary,
            fontSize: 9,
            fontWeight: FontWeight.w600,
            letterSpacing: 1,
          ),
        ),
      ],
    );
  }
}
