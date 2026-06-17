import 'package:flutter/material.dart';
import '../../config/app_colors.dart';
import '../../config/localization.dart';
import '../../services/ai_service.dart';
import '../../services/supabase_service.dart';

class AiChatScreen extends StatefulWidget {
  const AiChatScreen({super.key});

  @override
  State<AiChatScreen> createState() => _AiChatScreenState();
}

class _AiChatScreenState extends State<AiChatScreen> {
  final TextEditingController _controller = TextEditingController();
  final ScrollController _scrollController = ScrollController();
  final AiService _aiService = AiService();
  final SupabaseService _supabaseService = SupabaseService.instance;
  
  final List<Map<String, String>> _messages = [];
  bool _isTyping = false;
  String _context = '';

  @override
  void initState() {
    super.initState();
    _loadGlobalContext();
  }

  Future<void> _loadGlobalContext() async {
    // Only load context, don't generate a welcome message anymore
    try {
      final history = await _supabaseService.getSensorDataHistory();
      final artifacts = await _supabaseService.getArtifacts();
      
      String env = history.isNotEmpty 
          ? 'Temp: ${history.last.temperature}°C, Hum: ${history.last.humidity}%, Air: ${history.last.airQuality}PPM'
          : 'No sensor data';
      
      String arts = artifacts.map((a) => '${a.name} (${a.condition}, ${a.crackPercentage}% cracks)').join(', ');
      _context = 'Museum Status: $env. Collection: $arts.';
    } catch (_) {}
  }

  void _sendMessage() async {
    if (_controller.text.trim().isEmpty) return;
    
    final userText = _controller.text.trim();
    _controller.clear();
    
    setState(() {
      _messages.add({'role': 'user', 'text': userText});
      _isTyping = true;
    });
    
    _scrollToBottom();
    
    final response = await _aiService.askAiQuestion(userText, _context);
    
    if (mounted) {
      setState(() {
        _messages.add({'role': 'ai', 'text': response});
        _isTyping = false;
      });
      _scrollToBottom();
    }
  }

  void _scrollToBottom() {
    // With reverse: true, we don't need to manually scroll to the bottom of the list
    // because the "bottom" is the index 0.
    if (_scrollController.hasClients) {
      _scrollController.animateTo(
        0,
        duration: const Duration(milliseconds: 300),
        curve: Curves.easeOut,
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        automaticallyImplyLeading: false,
        title: Column(
          children: [
            Text(
              RU.globalAiChat,
              style: const TextStyle(color: AppColors.textPrimary, fontSize: 16, fontWeight: FontWeight.w800),
            ),
            const Text(
              'Gemini 3.1 Flash-Lite Engine',
              style: TextStyle(color: AppColors.mint, fontSize: 10, fontWeight: FontWeight.bold),
            ),
          ],
        ),
        centerTitle: true,
      ),
      body: Column(
        children: [
          Expanded(
            child: _messages.isEmpty 
              ? _buildEmptyState()
              : ListView.builder(
                  controller: _scrollController,
                  reverse: true,
                  padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
                  itemCount: _messages.length,
                  itemBuilder: (context, index) {
                    final msg = _messages[_messages.length - 1 - index];
                    return _buildMessageBubble(msg['role'] == 'user', msg['text']!);
                  },
                ),
          ),
          if (_isTyping)
            Padding(
              padding: const EdgeInsets.only(left: 24, bottom: 16),
              child: Row(
                children: [
                  const SizedBox(
                    width: 12,
                    height: 12,
                    child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.mint),
                  ),
                  const SizedBox(width: 12),
                  Text(
                    'ИИ анализирует базу данных...',
                    style: TextStyle(color: AppColors.textTertiary, fontSize: 12, fontStyle: FontStyle.italic),
                  ),
                ],
              ),
            ),
          _buildInputArea(),
        ],
      ),
    );
  }

  Widget _buildMessageBubble(bool isUser, String text) {
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(bottom: 20),
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.8),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: isUser ? AppColors.mint : AppColors.surface,
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(20),
            topRight: const Radius.circular(20),
            bottomLeft: Radius.circular(isUser ? 20 : 0),
            bottomRight: Radius.circular(isUser ? 0 : 20),
          ),
          border: isUser ? null : Border.all(color: AppColors.cardBorder),
          boxShadow: [
            BoxShadow(
              color: (isUser ? AppColors.mint : Colors.black).withValues(alpha: 0.1),
              blurRadius: 10,
              offset: const Offset(0, 4),
            ),
          ],
        ),
        child: Text(
          text,
          style: TextStyle(
            color: isUser ? Colors.black : AppColors.textPrimary,
            fontSize: 14,
            height: 1.5,
            fontWeight: isUser ? FontWeight.w600 : FontWeight.w500,
          ),
        ),
      ),
    );
  }

  Widget _buildInputArea() {
    return Container(
      padding: EdgeInsets.fromLTRB(24, 16, 24, MediaQuery.of(context).viewInsets.bottom > 0 ? 16 : MediaQuery.of(context).padding.bottom + 16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border(top: BorderSide(color: AppColors.cardBorder, width: 0.5)),
      ),
      child: Row(
        children: [
          Expanded(
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 20),
              decoration: BoxDecoration(
                color: AppColors.background,
                borderRadius: BorderRadius.circular(24),
                border: Border.all(color: AppColors.cardBorder),
              ),
              child: TextField(
                controller: _controller,
                style: const TextStyle(color: AppColors.textPrimary, fontSize: 14),
                decoration: InputDecoration(
                  hintText: 'Спросите ИИ об артефактах...',
                  hintStyle: TextStyle(color: AppColors.textTertiary, fontSize: 14),
                  border: InputBorder.none,
                ),
                onSubmitted: (_) => _sendMessage(),
              ),
            ),
          ),
          const SizedBox(width: 12),
          GestureDetector(
            onTap: _sendMessage,
            child: Container(
              padding: const EdgeInsets.all(14),
              decoration: const BoxDecoration(
                color: AppColors.mint,
                shape: BoxShape.circle,
              ),
              child: const Icon(Icons.send_rounded, color: Colors.black, size: 20),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildEmptyState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Container(
            padding: const EdgeInsets.all(24),
            decoration: BoxDecoration(
              color: AppColors.mint.withValues(alpha: 0.1),
              shape: BoxShape.circle,
            ),
            child: const Icon(Icons.psychology_rounded, color: AppColors.mint, size: 64),
          ),
          const SizedBox(height: 24),
          const Text(
            'ArchoAI Помощник',
            style: TextStyle(color: AppColors.textPrimary, fontSize: 20, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 12),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 40),
            child: Text(
              'Спросите меня о состоянии коллекции, параметрах климата или рекомендациях по реставрации.',
              textAlign: TextAlign.center,
              style: TextStyle(color: AppColors.textSecondary.withValues(alpha: 0.7), fontSize: 14),
            ),
          ),
        ],
      ),
    );
  }
}
