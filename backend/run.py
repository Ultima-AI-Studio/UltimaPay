"""Run the standalone UltimaPay API with one command."""

import uvicorn

from app.config import host, port


def main() -> None:
    uvicorn.run("app.main:app", host=host(), port=port(), reload=False)


if __name__ == "__main__":
    main()

