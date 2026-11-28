import 'package:flutter/material.dart';

import '../config/app_colors.dart';

/// Angle of photo [index] when [count] photos cover one full turn of the
/// turntable (6 photos: 0, 60, ..., 300 degrees).
String angleLabel(int index, int count) =>
    count <= 0 ? '' : '${(index * 360 / count).round()}°';

/// A row of small thumbnails, one per turntable angle. Tapping one opens
/// [AngleGalleryScreen] at that photo.
class AngleStrip extends StatelessWidget {
  const AngleStrip({super.key, required this.urls, this.title = 'Ракурсы'});

  final List<String> urls;
  final String title;

  @override
  Widget build(BuildContext context) {
    if (urls.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '$title: ${urls.length}',
            style: const TextStyle(color: AppColors.textTertiary, fontSize: 12, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 8),
          SizedBox(
            height: 72,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              itemCount: urls.length,
              separatorBuilder: (_, _) => const SizedBox(width: 8),
              itemBuilder: (context, index) => GestureDetector(
                key: ValueKey('angle-thumb-$index'),
                onTap: () => Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) => AngleGalleryScreen(urls: urls, initialIndex: index),
                )),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(10),
                  child: Stack(
                    children: [
                      Image.network(
                        urls[index],
                        width: 72,
                        height: 72,
                        fit: BoxFit.cover,
                        errorBuilder: (_, _, _) => Container(
                          width: 72,
                          height: 72,
                          color: AppColors.surface,
                          child: const Icon(Icons.broken_image_outlined, color: AppColors.textTertiary),
                        ),
                      ),
                      Positioned(
                        left: 4,
                        bottom: 4,
                        child: _AngleBadge(label: angleLabel(index, urls.length)),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// Full-screen viewer of all angle photos: swipe between them, pinch to zoom.
class AngleGalleryScreen extends StatefulWidget {
  const AngleGalleryScreen({super.key, required this.urls, this.initialIndex = 0});

  final List<String> urls;
  final int initialIndex;

  @override
  State<AngleGalleryScreen> createState() => _AngleGalleryScreenState();
}

class _AngleGalleryScreenState extends State<AngleGalleryScreen> {
  late final PageController _controller = PageController(initialPage: widget.initialIndex);
  late int _index = widget.initialIndex;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: Text('${angleLabel(_index, widget.urls.length)}  ·  ${_index + 1} / ${widget.urls.length}'),
      ),
      body: PageView.builder(
        controller: _controller,
        itemCount: widget.urls.length,
        onPageChanged: (index) => setState(() => _index = index),
        itemBuilder: (context, index) => InteractiveViewer(
          maxScale: 5,
          child: Center(
            child: Image.network(
              widget.urls[index],
              fit: BoxFit.contain,
              loadingBuilder: (context, child, progress) =>
                  progress == null ? child : const Center(child: CircularProgressIndicator(color: AppColors.mint)),
              errorBuilder: (_, _, _) =>
                  const Icon(Icons.broken_image_outlined, color: Colors.white54, size: 64),
            ),
          ),
        ),
      ),
    );
  }
}

class _AngleBadge extends StatelessWidget {
  const _AngleBadge({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
      decoration: BoxDecoration(color: Colors.black.withValues(alpha: 0.6), borderRadius: BorderRadius.circular(6)),
      child: Text(label, style: const TextStyle(color: Colors.white, fontSize: 10, fontWeight: FontWeight.w700)),
    );
  }
}
