import 'dart:async';
import 'dart:convert';

// --- SIMPLE FUNCTIONS ---

String greet(String name) {
  return 'Hello, $name!';
}

int add(int a, int b) {
  return a + b;
}

// --- ASYNC FUNCTION ---

Future<Map<String, dynamic>> fetchUserProfile(String userId) async {
  final response = await http.get(Uri.parse('/api/users/$userId'));
  if (response.statusCode != 200) {
    throw Exception('Failed to fetch user: ${response.statusCode}');
  }
  return jsonDecode(response.body);
}

// --- CLASS WITH METHODS ---

class AuthService {
  final String baseUrl;
  String? _token;

  AuthService(this.baseUrl);

  // Named constructor
  AuthService.withToken(this.baseUrl, this._token);

  Future<String> login(String email, String password) async {
    final response = await http.post(
      Uri.parse('$baseUrl/auth/login'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'email': email, 'password': password}),
    );

    if (response.statusCode != 200) {
      throw Exception('Authentication failed');
    }

    final data = jsonDecode(response.body);
    _token = data['token'];
    return _token!;
  }

  void logout() {
    _token = null;
  }

  bool get isAuthenticated => _token != null;

  String? getToken() {
    return _token;
  }
}

// --- ABSTRACT CLASS ---

abstract class Repository<T> {
  Future<T> findById(String id);
  Future<List<T>> findAll();
  Future<void> save(T entity);
  Future<void> delete(String id);
}

// --- CLASS WITH INHERITANCE + MIXINS ---

mixin Loggable {
  void log(String message) {
    print('[${DateTime.now()}] $message');
  }
}

class UserRepository extends Repository<Map<String, dynamic>> with Loggable {
  final String apiUrl;
  final AuthService authService;

  UserRepository(this.apiUrl, this.authService);

  @override
  Future<Map<String, dynamic>> findById(String id) async {
    log('Fetching user: $id');
    final token = authService.getToken();
    final response = await http.get(
      Uri.parse('$apiUrl/users/$id'),
      headers: {'Authorization': 'Bearer $token'},
    );
    return jsonDecode(response.body);
  }

  @override
  Future<List<Map<String, dynamic>>> findAll() async {
    log('Fetching all users');
    final token = authService.getToken();
    final response = await http.get(
      Uri.parse('$apiUrl/users'),
      headers: {'Authorization': 'Bearer $token'},
    );
    final List<dynamic> data = jsonDecode(response.body);
    return data.cast<Map<String, dynamic>>();
  }

  @override
  Future<void> save(Map<String, dynamic> entity) async {
    log('Saving user: ${entity['id']}');
    final token = authService.getToken();
    await http.post(
      Uri.parse('$apiUrl/users'),
      headers: {
        'Authorization': 'Bearer $token',
        'Content-Type': 'application/json',
      },
      body: jsonEncode(entity),
    );
  }

  @override
  Future<void> delete(String id) async {
    log('Deleting user: $id');
    final token = authService.getToken();
    await http.delete(
      Uri.parse('$apiUrl/users/$id'),
      headers: {'Authorization': 'Bearer $token'},
    );
  }
}

// --- FUNCTION WITH NAMED PARAMETERS ---

Map<String, dynamic> createUser({
  required String name,
  required String email,
  String role = 'user',
  bool isActive = true,
}) {
  return {
    'id': DateTime.now().millisecondsSinceEpoch.toString(),
    'name': name,
    'email': email,
    'role': role,
    'isActive': isActive,
  };
}

// --- ENUM ---

enum UserRole {
  admin,
  moderator,
  user;

  String get displayName {
    switch (this) {
      case UserRole.admin:
        return 'Administrator';
      case UserRole.moderator:
        return 'Moderator';
      case UserRole.user:
        return 'Regular User';
    }
  }
}
