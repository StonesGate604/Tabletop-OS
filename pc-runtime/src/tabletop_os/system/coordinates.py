"""Transform between calibrated spaces through common camera pixels."""


def project_between(position, source, destination):
    """Reproject a point without applying LED output rotation or reflection."""
    import numpy as np

    pixel = np.linalg.solve(np.asarray(source.matrix), np.array([position.x, position.y, 1.0]))
    if abs(pixel[2]) < 1e-12:
        raise ValueError('Point maps to camera infinity')
    return destination.map(float(pixel[0]/pixel[2]), float(pixel[1]/pixel[2]))
