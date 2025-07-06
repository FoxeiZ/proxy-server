from proxy import create_app
from proxy.config import Config

app = create_app()
if __name__ == "__main__":
    app.run(host=Config.host, port=Config.port, use_reloader=False, debug=False)
