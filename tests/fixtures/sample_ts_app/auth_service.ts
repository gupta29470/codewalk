// --- SIMPLE FUNCTIONS ---

function greet(name: string): string {
    return `Hello, ${name}!`;
}

function add(a: number, b: number): number {
    return a + b;
}

// --- ASYNC FUNCTION ---

async function fetchUserProfile(userId: string): Promise<User> {
    const response = await fetch(`/api/users/${userId}`);
    if (!response.ok) {
        throw new Error(`Failed to fetch user: ${response.status}`);
    }
    return response.json();
}

// --- INTERFACE ---

interface User {
    id: string;
    name: string;
    email: string;
    role: "admin" | "user" | "moderator";
}

// --- CLASS WITH METHODS ---

class AuthService {
    private tokenKey = "auth_token";
    private baseUrl: string;

    constructor(baseUrl: string) {
        this.baseUrl = baseUrl;
    }

    async login(email: string, password: string): Promise<string> {
        const response = await fetch(`${this.baseUrl}/auth/login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email, password }),
        });

        if (!response.ok) {
            throw new Error("Authentication failed");
        }

        const data = await response.json();
        localStorage.setItem(this.tokenKey, data.token);
        return data.token;
    }

    logout(): void {
        localStorage.removeItem(this.tokenKey);
    }

    isAuthenticated(): boolean {
        return localStorage.getItem(this.tokenKey) !== null;
    }

    getToken(): string | null {
        return localStorage.getItem(this.tokenKey);
    }
}

// --- GENERIC FUNCTION ---

function filterByProperty<T>(items: T[], key: keyof T, value: T[keyof T]): T[] {
    return items.filter((item) => item[key] === value);
}

// --- FUNCTION WITH DESTRUCTURED PARAMS ---

function createUser({ name, email, role }: { name: string; email: string; role: string }): User {
    return {
        id: crypto.randomUUID(),
        name,
        email,
        role: role as User["role"],
    };
}

// --- CLASS WITH INHERITANCE ---

class AdminService extends AuthService {
    private adminEndpoint: string;

    constructor(baseUrl: string, adminEndpoint: string) {
        super(baseUrl);
        this.adminEndpoint = adminEndpoint;
    }

    async getAllUsers(): Promise<User[]> {
        const token = this.getToken();
        if (!token) {
            throw new Error("Not authenticated");
        }

        const response = await fetch(`${this.adminEndpoint}/users`, {
            headers: { Authorization: `Bearer ${token}` },
        });

        return response.json();
    }

    async deleteUser(userId: string): Promise<void> {
        const token = this.getToken();
        await fetch(`${this.adminEndpoint}/users/${userId}`, {
            method: "DELETE",
            headers: { Authorization: `Bearer ${token}` },
        });
    }
}
