"""Model training for the risk engine.

The serving path never imports this package; it only loads the artifacts it
produces. That boundary keeps scikit-learn's training-time API surface out of
the request path and makes the artifact the single contract between the two.
"""
