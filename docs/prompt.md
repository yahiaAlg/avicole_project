You are a senior Django backend engineer tasked with building a production-ready a web-based internal Factory Management System for a small Algerian manufacturing plant to replace fragmented manual workflows. You will receive a Markdown specification document that serves as your complete requirements reference.

**Your Task:**
Generate a complete Django backend implementation based strictly on the provided specification document. Do not add features, functionality, or assumptions beyond what is explicitly described in the spec.

**Technical Requirements:**

- Use function-based views exclusively (no class-based views)
- Implement GET/POST request handling with Post-Redirect-Get pattern
- Use minimal AJAX via JsonResponse only when the spec explicitly requires it (fetch/XHR requests only)
- Utilize Django's built-in User model with a OneToOne Profile relationship
- Generate printable invoice/receipt pages using dedicated URLs that render HTML templates with print-specific CSS (@media print rules)
- No PDF generation libraries (no ReportLab, WeasyPrint, etc.)

**Implementation Scope:**
You must fully implement all of the following Django components:

1. **Models** - Complete model definitions with proper relationships, fields, and constraints
2. **Signals** - Django signals for automated workflows (if specified)
3. **Utilities** - Helper functions and business logic utilities
4. **Django-import-export Resources** - For data import/export functionality (if specified)
5. **Forms** - Django forms with proper validation
6. **View Logic** - Complete function-based views with proper error handling
7. **URL Patterns** - Clean URL routing structure
8. **Admin Configuration** - Django admin interface setup
9. **Templates** - HTML templates including print-optimized versions for invoices/receipts

**Code Organization:**

- Structure code cleanly by Django app
- Follow Django best practices for file organization
- Use descriptive naming conventions
- Include proper error handling and validation

**Output Format:**
Provide the complete implementation organized by:

1. Project structure overview
2. Each Django app with its complete file contents
3. Settings and configuration files
4. Any additional setup instructions

Wait for the Markdown specification document, then generate the complete Django backend implementation that precisely matches the requirements without adding extra features.
no templates , just the backend python files

and for company information do them in a core app in company information You will receive a Markdown specification document that serves as your complete requirements reference.

**Your Task:**
Generate a complete Django backend implementation based strictly on the provided specification document. Do not add features, functionality, or assumptions beyond what is explicitly described in the spec.

**Technical Requirements:**

- Use function-based views exclusively (no class-based views)
- Implement GET/POST request handling with Post-Redirect-Get pattern
- Use minimal AJAX via JsonResponse only when the spec explicitly requires it (fetch/XHR requests only)
- Utilize Django's built-in User model with a OneToOne Profile relationship
- Generate printable invoice/receipt pages using dedicated URLs that render HTML templates with print-specific CSS (@media print rules)
- No PDF generation libraries (no ReportLab, WeasyPrint, etc.)

**Implementation Scope:**
You must fully implement all of the following Django components:

1. **Models** - Complete model definitions with proper relationships, fields, and constraints
2. **Signals** - Django signals for automated workflows (if specified)
3. **Utilities** - Helper functions and business logic utilities
4. **Django-import-export Resources** - For data import/export functionality (if specified)
5. **Forms** - Django forms with proper validation
6. **View Logic** - Complete function-based views with proper error handling
7. **URL Patterns** - Clean URL routing structure
8. **Admin Configuration** - Django admin interface setup
9. **Templates** - HTML templates including print-optimized versions for invoices/receipts

**Code Organization:**

- Structure code cleanly by Django app
- Follow Django best practices for file organization
- Use descriptive naming conventions
- Include proper error handling and validation

**Output Format:**
Provide the complete implementation organized by:

1. Project structure overview
2. Each Django app with its complete file contents
3. Settings and configuration files
4. Any additional setup instructions

Wait for the Markdown specification document, then generate the complete Django backend implementation that precisely matches the requirements without adding extra features.
no templates , just the backend python files

and for the company information do them in a core app in company information

---

now Generate the Django code as text files you can download — but these would be static .py files stored in the project, not executable Django code
as what the prompt described
