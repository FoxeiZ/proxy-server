from proxy import create_app
from proxy.config import Config

if Config.debug:
    import tracemalloc
    import warnings

    tracemalloc.start()
    warnings.filterwarnings("error", category=RuntimeWarning)


app = create_app()
if __name__ == "__main__":
    app.run(
        host=Config.host,
        port=Config.port,
        use_reloader=False,
        debug=False,
    )
