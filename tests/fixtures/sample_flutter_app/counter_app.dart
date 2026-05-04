import 'dart:async';

import 'package:flutter/material.dart';

// --- STATELESS WIDGET ---

class GreetingCard extends StatelessWidget {
  final String name;
  final String message;

  const GreetingCard({
    super.key,
    required this.name,
    this.message = 'Welcome!',
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(name, style: Theme.of(context).textTheme.headlineMedium),
            const SizedBox(height: 8),
            Text(message),
          ],
        ),
      ),
    );
  }
}

// --- STATEFUL WIDGET ---

class CounterPage extends StatefulWidget {
  final int initialCount;
  final String title;

  const CounterPage({super.key, this.initialCount = 0, required this.title});

  @override
  State<CounterPage> createState() => _CounterPageState();
}

class _CounterPageState extends State<CounterPage> {
  late int _count;
  bool _isLoading = false;

  @override
  void initState() {
    super.initState();
    _count = widget.initialCount;
  }

  void _increment() {
    setState(() {
      _count++;
    });
  }

  void _decrement() {
    if (_count > 0) {
      setState(() {
        _count--;
      });
    }
  }

  Future<void> _resetWithDelay() async {
    setState(() => _isLoading = true);
    await Future.delayed(const Duration(seconds: 1));
    setState(() {
      _count = 0;
      _isLoading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(widget.title)),
      body: Center(
        child: _isLoading
            ? const CircularProgressIndicator()
            : Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Text(
                    'Count: $_count',
                    style: Theme.of(context).textTheme.headlineLarge,
                  ),
                  const SizedBox(height: 24),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      ElevatedButton(
                        onPressed: _decrement,
                        child: const Icon(Icons.remove),
                      ),
                      const SizedBox(width: 16),
                      ElevatedButton(
                        onPressed: _increment,
                        child: const Icon(Icons.add),
                      ),
                    ],
                  ),
                ],
              ),
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: _resetWithDelay,
        child: const Icon(Icons.refresh),
      ),
    );
  }
}

// --- PROVIDER / SERVICE CLASS ---

class TodoService extends ChangeNotifier {
  final List<Map<String, dynamic>> _todos = [];

  List<Map<String, dynamic>> get todos => List.unmodifiable(_todos);
  int get count => _todos.length;

  void addTodo(String title, {String priority = 'medium'}) {
    _todos.add({
      'id': DateTime.now().millisecondsSinceEpoch.toString(),
      'title': title,
      'priority': priority,
      'completed': false,
    });
    notifyListeners();
  }

  void toggleTodo(String id) {
    final index = _todos.indexWhere((todo) => todo['id'] == id);
    if (index != -1) {
      _todos[index]['completed'] = !_todos[index]['completed'];
      notifyListeners();
    }
  }

  void removeTodo(String id) {
    _todos.removeWhere((todo) => todo['id'] == id);
    notifyListeners();
  }

  List<Map<String, dynamic>> getByPriority(String priority) {
    return _todos.where((todo) => todo['priority'] == priority).toList();
  }
}

// --- HELPER FUNCTION ---

ThemeData buildAppTheme({bool isDark = false}) {
  return ThemeData(
    brightness: isDark ? Brightness.dark : Brightness.light,
    colorSchemeSeed: Colors.blue,
    useMaterial3: true,
  );
}

// --- MAIN APP ---

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Flutter Demo',
      theme: buildAppTheme(),
      darkTheme: buildAppTheme(isDark: true),
      home: const CounterPage(title: 'Counter'),
    );
  }
}
