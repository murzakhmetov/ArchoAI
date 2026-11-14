import 'dart:io';
import 'dart:async';
import 'package:url_launcher/url_launcher.dart';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import '../../config/app_colors.dart';
import '../../config/localization.dart';
import '../../models/artifact_model.dart';
import '../../services/supabase_service.dart';
import '../../services/ai_service.dart';
import '../../services/tripo_service.dart';
import '../../services/local_storage_service.dart';
import '../../services/model_storage.dart';
import '../scanner/model_viewer_screen.dart';
import '../../widgets/glass_card.dart';

class ArtifactsScreen extends StatefulWidget {
  const ArtifactsScreen({super.key});

  @override
  State<ArtifactsScreen> createState() => _ArtifactsScreenState();
}

class _ArtifactsScreenState extends State<ArtifactsScreen> {
  final SupabaseService _supabaseService = SupabaseService.instance;
  final AiService _aiService = AiService();
  final ImagePicker _picker = ImagePicker();
  
  Future<void> _launchMaps() async {
    final Uri url = Uri.parse('https://www.google.com/maps/search/?api=1&query=Nazarbayev+Intellectual+School+Almaty+Zhamakaeva+145');
    if (!await launchUrl(url, mode: LaunchMode.externalApplication)) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Не удалось открыть карты')),
        );
      }
    }
  }

  List<ArtifactModel> _artifacts = [];
  bool _isLoading = true;
  bool _isAiAnalyzing = false;
  int _aiProgress = 0;
  String _aiStatus = '';
  String? _uploadingArtifactId;
  StreamSubscription? _artifactsSubscription;

  @override
  void initState() {
    super.initState();
    _loadArtifacts();
    _setupRealtime();
  }

  void _setupRealtime() {
    _artifactsSubscription = _supabaseService.streamArtifacts().listen(
      (updatedList) {
        if (!mounted) return;
        setState(() {
          _artifacts = updatedList;
          _isLoading = false;
        });
      },
      // A realtime hiccup must not become an unhandled async error.
      onError: (Object e) => debugPrint('Artifacts stream error: $e'),
    );
  }

  @override
  void dispose() {
    _artifactsSubscription?.cancel();
    super.dispose();
  }

  Future<void> _loadArtifacts() async {
    setState(() => _isLoading = true);
    try {
      final data = await _supabaseService.getArtifacts();
      if (!mounted) return;
      setState(() {
        _artifacts = data;
        _isLoading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _isLoading = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Ошибка загрузки: $e')),
      );
    }
  }

  /// Camera/gallery chooser shared by every flow on this screen.
  Future<ImageSource?> _pickImageSource(String title, {String? subtitle}) {
    return showModalBottomSheet<ImageSource>(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (ctx) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 40,
                height: 4,
                decoration: BoxDecoration(
                  color: AppColors.textTertiary,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
              const SizedBox(height: 20),
              Text(
                title,
                style: const TextStyle(
                  color: AppColors.textPrimary,
                  fontSize: 18,
                  fontWeight: FontWeight.w700,
                ),
              ),
              if (subtitle != null) ...[
                const SizedBox(height: 4),
                Text(
                  subtitle,
                  style: const TextStyle(
                    color: AppColors.textSecondary,
                    fontSize: 13,
                  ),
                ),
              ],
              const SizedBox(height: 20),
              ListTile(
                leading: Container(
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: AppColors.mint.withValues(alpha: 0.1),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: const Icon(Icons.camera_alt_rounded, color: AppColors.mint),
                ),
                title: Text(S.current.translate('Camera')),
                onTap: () => Navigator.pop(ctx, ImageSource.camera),
              ),
              ListTile(
                leading: Container(
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: AppColors.cyan.withValues(alpha: 0.1),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: const Icon(Icons.photo_library_rounded, color: AppColors.cyan),
                ),
                title: Text(S.current.translate('Gallery')),
                onTap: () => Navigator.pop(ctx, ImageSource.gallery),
              ),
            ],
          ),
        ),
      ),
    );
  }

  /// Keeps the Tripo task of [artifactId] on this device (and in the list).
  /// A started task is already paid for: with its id the model can still be
  /// fetched later if polling or the download fails or the app is closed.
  Future<void> _rememberTask(String artifactId, String taskId) async {
    try {
      await LocalStorageService.instance.saveTripoTaskId(artifactId, taskId);
    } catch (e) {
      debugPrint('Could not remember Tripo task $taskId: $e');
    }
    if (!mounted) return;
    setState(() {
      final idx = _artifacts.indexWhere((a) => a.id == artifactId);
      if (idx != -1) {
        _artifacts[idx] = _artifacts[idx].copyWith(tripoTaskId: taskId);
      }
    });
  }

  /// Mirrors the Tripo task id to Supabase (null clears it), so another
  /// device can recover a paid model too. Best effort.
  Future<void> _syncTaskId(String artifactId, String? taskId) async {
    try {
      await _supabaseService.updateArtifactTaskId(artifactId, taskId);
    } catch (e) {
      debugPrint('Could not sync the Tripo task id of $artifactId: $e');
    }
  }

  /// Drops the stored task once its model is saved or the task is dead.
  /// [clearRemote] false keeps the id in Supabase until the cloud copy exists.
  Future<void> _forgetTask(String artifactId, {bool clearRemote = true}) async {
    try {
      await LocalStorageService.instance.clearTripoTaskId(artifactId);
    } catch (e) {
      debugPrint('Could not clear the Tripo task of $artifactId: $e');
    }
    if (clearRemote) await _syncTaskId(artifactId, null);
    if (!mounted) return;
    setState(() {
      final idx = _artifacts.indexWhere((a) => a.id == artifactId);
      if (idx != -1) {
        _artifacts[idx] = _artifacts[idx].copyWith(clearTripoTaskId: true);
      }
    });
  }

  /// Copies the saved model into the Supabase batches so other devices can
  /// open it. Never fails the flow: the model is already safe on this device.
  Future<void> _publishModel(
    ArtifactModel artifact,
    String localPath,
    String taskId,
  ) async {
    if (!isDatabaseId(artifact.id)) return;
    try {
      if (mounted) {
        setState(() {
          _aiStatus = 'Uploading 3D model to the cloud...';
          _aiProgress = 98;
        });
      }
      final stored = await _supabaseService.uploadArtifactModel(
        artifact.id,
        localPath,
        tripoTaskId: taskId,
      );
      await _supabaseService.attachModelToArtifact(artifact.id, stored);
      if (!mounted) return;
      setState(() {
        final idx = _artifacts.indexWhere((a) => a.id == artifact.id);
        if (idx != -1) {
          _artifacts[idx] = _artifacts[idx].copyWith(modelUrl: stored.url);
        }
      });
    } on ModelStorageException catch (e) {
      _showSnack('The model is saved on this device only. ${e.message}');
    } catch (e) {
      debugPrint('Cloud upload of the model failed: $e');
      _showSnack('The model is saved on this device only: cloud upload failed ($e).');
    }
  }

  void _showSnack(String message, {Color? background}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message), backgroundColor: background),
    );
  }

  /// Generates a 3D model from [imageFile] and keeps the GLB on this device.
  ///
  /// Returns true on success. A failure is reported with a snackbar and does
  /// not abort the surrounding flow, because the artifact record already exists.
  Future<bool> _generateAndSave3D(ArtifactModel artifact, File imageFile) async {
    try {
      if (mounted) {
        setState(() {
          _aiStatus = 'Initializing 3D Scan...';
          _aiProgress = 10;
        });
      }

      final taskId = await TripoService.instance.createImageToModelTask(imageFile);
      // Paid for from here on: remember the task before waiting for it.
      await _rememberTask(artifact.id, taskId);
      await _syncTaskId(artifact.id, taskId);

      final result = await TripoService.instance.waitForTask(
        taskId,
        isCancelled: () => !mounted,
        onProgress: (p) {
          if (!mounted) return;
          setState(() {
            _aiProgress = 10 + (p * 0.8).toInt();
            _aiStatus = 'Generating 3D Mesh ($p%)...';
          });
        },
      );

      final modelUrl = result.modelUrl;
      if (modelUrl == null) {
        throw TripoException('Tripo finished the task but returned no model.');
      }

      if (mounted) {
        setState(() {
          _aiStatus = 'Downloading 3D model...';
          _aiProgress = 95;
        });
      }

      // Tripo links are temporary: keep the GLB on the device right away. The
      // link itself is not written to Supabase, where it would soon be dead.
      final localPath =
          await LocalStorageService.instance.getModelSavePath(artifact.id);
      try {
        await TripoService.instance
            .downloadTaskModel(taskId, localPath, knownUrl: modelUrl);
      } catch (e) {
        // The model exists and was paid for. The task id stays stored, so the
        // artifact keeps its 3D button and "Save to device" can try again.
        debugPrint('Could not save the generated model: $e');
        _showSnack(
          'The 3D model is ready but could not be saved to the device: $e. '
          'Open the artifact and tap "Save to device".',
        );
        return false;
      }

      // The model is on this device now. The id stays in Supabase until the
      // cloud copy below exists, so other devices could still recover it.
      await _forgetTask(artifact.id, clearRemote: false);
      if (mounted) {
        setState(() {
          final idx = _artifacts.indexWhere((a) => a.id == artifact.id);
          if (idx != -1) {
            _artifacts[idx] = _artifacts[idx].copyWith(localModelPath: localPath);
          }
        });
      }
      await _publishModel(artifact, localPath, taskId);
      return true;
    } on TripoCancelledException {
      return false;
    } catch (e) {
      debugPrint('3D Generation Error: $e');
      // A task that ended in a terminal failure can never deliver a model.
      if (e is TripoException && e.taskFailed) await _forgetTask(artifact.id);
      // We don't fail the whole artifact creation if 3D fails, just show a minor error
      _showSnack('3D Scan failed: $e');
      return false;
    }
  }

  Future<void> _generate3DForExisting(ArtifactModel artifact) async {
    // Show picker to get a new photo for 3D generation
    final ImageSource? source = await _pickImageSource(
      'Фото для 3D модели',
      subtitle: artifact.name,
    );
    if (source == null) return;

    final XFile? image = await _picker.pickImage(
      source: source,
      maxWidth: 1024,
      maxHeight: 1024,
      imageQuality: 80,
    );
    if (image == null || !mounted) return;

    setState(() {
      _isAiAnalyzing = true;
      _aiProgress = 5;
      _aiStatus = 'Запуск 3D-сканирования...';
    });

    var created = false;
    try {
      created = await _generateAndSave3D(artifact, File(image.path));
    } finally {
      if (mounted) setState(() => _isAiAnalyzing = false);
    }

    // _generateAndSave3D already reported a failure, so only confirm success.
    if (created) {
      _showSnack('3D модель успешно создана!', background: AppColors.mint);
    }
  }

  Future<void> _analyzeAndCreateArtifact() async {
    final ImageSource? source = await _pickImageSource(S.current.addArtifact);
    if (source == null) return;

    final XFile? image = await _picker.pickImage(
      source: source,
      maxWidth: 1024,
      maxHeight: 1024,
      imageQuality: 80,
    );
    if (image == null || !mounted) return;

    setState(() {
      _isAiAnalyzing = true;
      _aiProgress = 0;
      _aiStatus = S.current.analyzing;
    });

    try {
      // 1. Analyze with Gemini
      final bytes = await image.readAsBytes();
      final aiData = await _aiService.analyzeArtifact(
        bytes,
        mimeType: _mimeTypeFor(image.path),
      );

      // 2. Upload photo to get URL
      final tempId = DateTime.now().millisecondsSinceEpoch.toString();
      final url = await _supabaseService.uploadArtifactImage(tempId, image.path);

      // 3. Save to DB
      final newArtifactData = {
        ...aiData,
        'image_path': url,
        'created_at': DateTime.now().toUtc().toIso8601String(),
        'status': 'cataloged',
      };

      final newArtifact = await _supabaseService.createArtifact(newArtifactData);
      if (!mounted) return;

      setState(() {
        // The realtime stream may already have delivered the new row.
        if (!_artifacts.any((a) => a.id == newArtifact.id)) {
          _artifacts.insert(0, newArtifact);
        }
      });

      // 4. Start 3D generation (will update state when done)
      final has3D = await _generateAndSave3D(newArtifact, File(image.path));
      if (!mounted) return;

      setState(() => _isAiAnalyzing = false);

      // A failed 3D step already showed its own message.
      if (has3D) {
        _showSnack(S.current.photoSuccess, background: AppColors.mint);
      }
    } catch (e) {
      if (!mounted) return;
      setState(() => _isAiAnalyzing = false);
      _showSnack('${S.current.analysisError}: $e', background: AppColors.red);
    }
  }

  String _mimeTypeFor(String path) =>
      path.toLowerCase().endsWith('.png') ? 'image/png' : 'image/jpeg';

  Future<void> _pickAndUploadImage(ArtifactModel artifact) async {
    final source = await _pickImageSource(
      artifact.imagePath.isEmpty ? S.current.addPhoto : S.current.changePhoto,
    );
    if (source == null) return;

    final XFile? image = await _picker.pickImage(
      source: source,
      maxWidth: 1920,
      maxHeight: 1920,
      imageQuality: 85,
    );
    if (image == null || !mounted) return;

    setState(() {
      _uploadingArtifactId = artifact.id;
      _isAiAnalyzing = true; // Use global loading for the 3D part too
      _aiStatus = 'Initiating 3D Scan...';
      _aiProgress = 0;
    });

    try {
      // The storage path is reused for a replaced photo, so add a version
      // query to stop Image.network from showing the cached old picture.
      final storedUrl =
          await _supabaseService.uploadArtifactImage(artifact.id, image.path);
      final url = '$storedUrl?v=${DateTime.now().millisecondsSinceEpoch}';
      await _supabaseService.updateArtifactImage(artifact.id, url);
      if (!mounted) return;

      setState(() {
        final idx = _artifacts.indexWhere((a) => a.id == artifact.id);
        if (idx != -1) {
          _artifacts[idx] = _artifacts[idx].copyWith(imagePath: url);
        }
        _uploadingArtifactId = null;
      });

      // Start 3D scan update
      await _generateAndSave3D(artifact, File(image.path));
      if (!mounted) return;

      setState(() => _isAiAnalyzing = false);
      _showSnack(S.current.photoSuccess);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _uploadingArtifactId = null;
        _isAiAnalyzing = false;
      });
      _showSnack('${S.current.photoError}: $e');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Stack(
      children: [
        Scaffold(
          backgroundColor: AppColors.background,
          body: RefreshIndicator(
            onRefresh: _loadArtifacts,
            color: AppColors.mint,
            backgroundColor: AppColors.surface,
            child: CustomScrollView(
              slivers: [
                SliverAppBar(
                  expandedHeight: 160,
                  floating: false,
                  pinned: true,
                  backgroundColor: AppColors.background,
                  elevation: 0,
                  flexibleSpace: FlexibleSpaceBar(
                    background: Container(
                      decoration: BoxDecoration(
                        gradient: LinearGradient(
                          colors: [
                            AppColors.mint.withValues(alpha: 0.1),
                            AppColors.background,
                          ],
                          begin: Alignment.topCenter,
                          end: Alignment.bottomCenter,
                        ),
                      ),
                    ),
                    title: Text(
                      RU.artifacts.toUpperCase(),
                      style: const TextStyle(
                        color: AppColors.textPrimary,
                        fontSize: 16,
                        fontWeight: FontWeight.w900,
                        letterSpacing: 4,
                      ),
                    ),
                    centerTitle: true,
                    titlePadding: const EdgeInsets.only(bottom: 24),
                  ),
                ),
                if (_isLoading)
                  const SliverFillRemaining(
                    child: Center(child: CircularProgressIndicator(color: AppColors.mint)),
                  )
                else if (_artifacts.isEmpty)
                  const SliverFillRemaining(
                    child: Center(
                      child: Text(
                        'Артефакты не найдены',
                        style: TextStyle(color: AppColors.textSecondary),
                      ),
                    ),
                  )
                else
                  SliverPadding(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                    sliver: SliverList(
                      delegate: SliverChildBuilderDelegate(
                        (context, index) => _buildArtifactCard(_artifacts[index]),
                        childCount: _artifacts.length,
                      ),
                    ),
                  ),
              ],
            ),
          ),
          floatingActionButton: FloatingActionButton(
            onPressed: _isAiAnalyzing ? null : _analyzeAndCreateArtifact,
            backgroundColor: AppColors.mint,
            elevation: 8,
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
            child: const Icon(Icons.add_a_photo_rounded, color: Colors.white),
          ),
        ),
        
        // AI Analyzing Overlay
        if (_isAiAnalyzing)
          Container(
            color: Colors.black.withValues(alpha: 0.8),
            child: Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Container(
                    width: 80,
                    height: 80,
                    padding: const EdgeInsets.all(4),
                    child: CircularProgressIndicator(
                      color: AppColors.mint, 
                      strokeWidth: 4,
                      value: _aiProgress > 0 ? _aiProgress / 100 : null,
                    ),
                  ),
                  const SizedBox(height: 24),
                  Text(
                    _aiProgress > 0 ? '$_aiStatus ($_aiProgress%)' : _aiStatus,
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 14,
                      fontWeight: FontWeight.w600,
                      letterSpacing: 0.5,
                      decoration: TextDecoration.none,
                    ),
                  ),
                  if (_aiProgress > 0) ...[
                    const SizedBox(height: 12),
                    Container(
                      width: 200,
                      height: 2,
                      decoration: BoxDecoration(
                        color: Colors.white.withValues(alpha: 0.1),
                        borderRadius: BorderRadius.circular(1),
                      ),
                      child: FractionallySizedBox(
                        alignment: Alignment.centerLeft,
                        widthFactor: _aiProgress / 100,
                        child: Container(
                          decoration: BoxDecoration(
                            color: AppColors.mint,
                            borderRadius: BorderRadius.circular(1),
                          ),
                        ),
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
      ],
    );
  }

  Widget _buildArtifactCard(ArtifactModel artifact) {
    final isUploading = _uploadingArtifactId == artifact.id;
    final hasImage = artifact.imagePath.isNotEmpty;
    // A stored Tripo task counts too: its model can still be fetched.
    final hasModel = (artifact.localModelPath != null && artifact.localModelPath!.isNotEmpty) ||
        (artifact.modelUrl != null && artifact.modelUrl!.isNotEmpty) ||
        (artifact.tripoTaskId != null && artifact.tripoTaskId!.isNotEmpty);

    return Container(
      margin: const EdgeInsets.only(bottom: 20),
      child: GlassCard(
        padding: EdgeInsets.zero,
        borderRadius: 20,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            GestureDetector(
              onTap: isUploading ? null : () => _pickAndUploadImage(artifact),
              child: Stack(
                children: [
                  if (hasImage)
                    Image.network(
                      artifact.imagePath,
                      height: 220,
                      width: double.infinity,
                      fit: BoxFit.cover,
                      errorBuilder: (context, error, stackTrace) =>
                          _buildImagePlaceholder(hasImage: false, isUploading: isUploading),
                    )
                  else
                    _buildImagePlaceholder(hasImage: false, isUploading: isUploading),

                  Positioned.fill(
                    child: DecoratedBox(
                      decoration: BoxDecoration(
                        gradient: LinearGradient(
                          colors: [Colors.black.withValues(alpha: 0.5), Colors.transparent],
                          begin: Alignment.bottomCenter,
                          end: Alignment.center,
                        ),
                      ),
                    ),
                  ),

                  if (isUploading)
                    Positioned.fill(
                      child: Container(
                        color: AppColors.background.withValues(alpha: 0.7),
                        child: Center(
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              const CircularProgressIndicator(color: AppColors.mint),
                              const SizedBox(height: 12),
                              Text(
                                S.current.photoUploading,
                                style: const TextStyle(
                                  color: AppColors.textPrimary,
                                  fontSize: 13,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),

                  if (!isUploading)
                    Positioned(
                      bottom: 12,
                      right: 12,
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                        decoration: BoxDecoration(
                          color: AppColors.surface.withValues(alpha: 0.85),
                          borderRadius: BorderRadius.circular(20),
                          border: Border.all(color: AppColors.mint.withValues(alpha: 0.3)),
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(
                              hasImage ? Icons.edit_rounded : Icons.add_a_photo_rounded,
                              color: AppColors.mint,
                              size: 14,
                            ),
                            const SizedBox(width: 6),
                            Text(
                              hasImage ? S.current.changePhoto : S.current.addPhoto,
                              style: const TextStyle(
                                color: AppColors.mint,
                                fontSize: 11,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                ],
              ),
            ),
            
            Padding(
              padding: const EdgeInsets.all(20),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Expanded(
                        child: Text(
                          artifact.name,
                          style: const TextStyle(
                            color: AppColors.textPrimary,
                            fontSize: 22,
                            fontWeight: FontWeight.w900,
                            letterSpacing: -0.5,
                          ),
                        ),
                      ),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                        decoration: BoxDecoration(
                          color: AppColors.mint.withValues(alpha: 0.1),
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(color: AppColors.mint.withValues(alpha: 0.2)),
                        ),
                        child: Text(
                          '${artifact.crackPercentage.toStringAsFixed(1)}%',
                          style: const TextStyle(
                            color: AppColors.mint,
                            fontSize: 12,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 20),
                  
                  _buildInfoTable(artifact),
                  
                  const SizedBox(height: 20),
                  const Divider(color: AppColors.cardBorder, thickness: 1),
                  const SizedBox(height: 12),
                  
                  // Location line
                  InkWell(
                    onTap: _launchMaps,
                    borderRadius: BorderRadius.circular(8),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(vertical: 4),
                      child: Row(
                        children: [
                          const Icon(Icons.location_on_rounded, color: AppColors.mint, size: 16),
                          const SizedBox(width: 8),
                          Text(
                            '${S.current.location}: ',
                            style: const TextStyle(color: AppColors.textTertiary, fontSize: 12),
                          ),
                          Flexible(
                            child: Text(
                              S.current.schoolName,
                              style: const TextStyle(
                                color: AppColors.mint, 
                                fontSize: 12, 
                                fontWeight: FontWeight.w700,
                                decoration: TextDecoration.underline,
                                decorationColor: AppColors.mint,
                              ),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 12),

                  // Purpose line
                  Row(
                    children: [
                      Icon(Icons.history_edu_rounded, color: AppColors.textTertiary.withValues(alpha: 0.5), size: 16),
                      const SizedBox(width: 8),
                      Text(
                        '${S.current.purpose}: ',
                        style: const TextStyle(color: AppColors.textTertiary, fontSize: 12),
                      ),
                      Flexible(
                        child: Text(
                          S.current.translate(artifact.purpose),
                          style: const TextStyle(color: AppColors.textSecondary, fontSize: 12, fontWeight: FontWeight.w600),
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 16),

                  // === Two Action Buttons ===
                  Row(
                    children: [
                      // ФОТО button
                      Expanded(
                        child: _buildActionButton(
                          icon: hasImage ? Icons.photo_camera_rounded : Icons.add_a_photo_rounded,
                          label: hasImage ? 'ФОТО ' : 'ДОБАВИТЬ ФОТО',
                          color: hasImage ? AppColors.mint : AppColors.textSecondary,
                          filled: hasImage,
                          onTap: isUploading ? null : () => _pickAndUploadImage(artifact),
                        ),
                      ),
                      const SizedBox(width: 12),
                      // 3D МОДЕЛЬ button
                      Expanded(
                        child: _buildActionButton(
                          icon: Icons.view_in_ar_rounded,
                          label: hasModel ? '3D МОДЕЛЬ ' : 'СОЗДАТЬ 3D',
                          color: hasModel ? AppColors.cyan : AppColors.textSecondary,
                          filled: hasModel,
                          onTap: hasModel
                              ? () => Navigator.of(context).push(
                                    MaterialPageRoute(builder: (_) => ModelViewerScreen(artifact: artifact)),
                                  )
                              : (_isAiAnalyzing ? null : () => _generate3DForExisting(artifact)),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildImagePlaceholder({required bool hasImage, required bool isUploading}) {
    return Container(
      height: 220,
      width: double.infinity,
      decoration: BoxDecoration(
        color: AppColors.surfaceLight,
        gradient: LinearGradient(
          colors: [AppColors.surfaceLight, AppColors.surface],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
      ),
      child: isUploading
          ? const SizedBox.shrink()
          : Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    Icons.add_a_photo_outlined,
                    color: AppColors.textTertiary.withValues(alpha: 0.4),
                    size: 48,
                  ),
                  const SizedBox(height: 8),
                  Text(
                    S.current.addPhoto,
                    style: TextStyle(
                      color: AppColors.textTertiary.withValues(alpha: 0.5),
                      fontSize: 13,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ],
              ),
            ),
    );
  }

  Widget _buildInfoTable(ArtifactModel artifact) {
    return Table(
      children: [
        TableRow(
          children: [
            _buildInfoItem(S.current.artifactType, S.current.translate(artifact.type)),
            _buildInfoItem(S.current.material, S.current.translate(artifact.material)),
          ],
        ),
        const TableRow(children: [SizedBox(height: 16), SizedBox(height: 16)]),
        TableRow(
          children: [
            _buildInfoItem(S.current.era, S.current.translate(artifact.era)),
            _buildInfoItem(S.current.condition, S.current.translate(artifact.condition), 
              color: artifact.condition.contains('Poor') ? AppColors.amber : AppColors.mint),
          ],
        ),
      ],
    );
  }

  Widget _buildInfoItem(String label, String value, {Color? color}) {
    return SizedBox(
      width: 120,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label.toUpperCase(),
            style: const TextStyle(
              color: AppColors.textTertiary,
              fontSize: 9,
              fontWeight: FontWeight.bold,
              letterSpacing: 1,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            value,
            style: TextStyle(
              color: color ?? AppColors.textPrimary,
              fontSize: 14,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildActionButton({
    required IconData icon,
    required String label,
    required Color color,
    required bool filled,
    required VoidCallback? onTap,
  }) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(12),
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 12),
          decoration: BoxDecoration(
            color: filled ? color.withValues(alpha: 0.1) : Colors.transparent,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(
              color: filled ? color.withValues(alpha: 0.3) : AppColors.cardBorder,
              width: 1,
            ),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(
                icon,
                size: 16,
                color: filled ? color : AppColors.textTertiary,
              ),
              const SizedBox(width: 8),
              Text(
                label,
                style: TextStyle(
                  color: filled ? color : AppColors.textTertiary,
                  fontSize: 10,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.5,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
