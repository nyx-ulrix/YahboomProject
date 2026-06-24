"""
Yahboom Dashboard Backend Application
"""

from flask import Flask


def create_app():
    """Application factory for creating Flask app instances"""
    app = Flask(__name__)

    # Register blueprints
    from app.routes.bot_routes import bot_bp
    from app.routes.stream_routes import stream_bp
    from app.routes.slam_routes import slam_bp
    from app.routes.vit_routes import vit_bp
    from app.routes.test_bench_routes import test_bench_bp
    app.register_blueprint(bot_bp)
    app.register_blueprint(stream_bp)
    app.register_blueprint(slam_bp)
    app.register_blueprint(vit_bp)
    app.register_blueprint(test_bench_bp)

    from app.routes.stream_routes import start_background_video_probe
    start_background_video_probe()

    # Start SLAM background service (auto-connects when main MQTT connects)
    from slam_service import slam_service
    slam_service.start_background()

    # Start VIT scene-decoder service (auto-connects when main MQTT connects)
    from app.services.vit.vit_service import vit_service
    vit_service.start_background()

    # Auto-connect the main MQTT service to the default broker on startup so the
    # backend (and the VIT monitor that mirrors it) is connected without needing
    # a browser to trigger /api/connect. Runs in a daemon thread so a slow or
    # unreachable broker never blocks app creation; the frontend retry loop keeps
    # trying if this initial attempt fails.
    import threading
    import logging
    from app.services.mqtt_service import mqtt_service
    from config import DEFAULT_BROKER_IP

    def _startup_autoconnect():
        try:
            success, message = mqtt_service.connect_to_broker(DEFAULT_BROKER_IP)
            if success:
                logging.getLogger(__name__).info(
                    "Startup auto-connect: %s", message)
            else:
                logging.getLogger(__name__).warning(
                    "Startup auto-connect failed: %s", message)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Startup auto-connect error: %s", exc)

    threading.Thread(
        target=_startup_autoconnect, daemon=True, name="startup-autoconnect"
    ).start()

    return app
