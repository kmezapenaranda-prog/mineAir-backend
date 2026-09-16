__all__ = ["app"]


def __getattr__(nombre):
    """Carga FastAPI solo cuando se solicita, sin conectar al importar el paquete."""
    if nombre == "app":
        from .api import app

        return app
    raise AttributeError(nombre)
