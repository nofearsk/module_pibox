from flask import Blueprint

# Create blueprints
anpr_bp = Blueprint('anpr', __name__)
api_bp = Blueprint('api', __name__)
web_bp = Blueprint('web', __name__)

# Import routes to register them
from . import anpr_routes
from . import api_routes
from . import web_routes
