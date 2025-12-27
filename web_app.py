from admin import admin_bp
from besthome_unified_bot import app as base_app, create_flask_app

app = base_app or create_flask_app()

if admin_bp.name not in app.blueprints:
    app.register_blueprint(admin_bp)
