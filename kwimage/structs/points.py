import numpy as np
import ubelt as ub
import skimage
import kwarray
from . import _generic


class _PointsWarpMixin:

    def _warp_imgaug(self, augmenter, input_shape, inplace=False):
        """
        Warps by applying an augmenter from the imgaug library

        Args:
            augmenter (imgaug.augmenters.Augmenter):
            input_shape (Tuple): h/w of the input image
            inplace (bool, default=False): if True, modifies data inplace

        Example:
            >>> from kwimage.structs.points import *  # NOQA
            >>> import imgaug
            >>> self = Points.random(10)
            >>> augmenter = imgaug.augmenters.Fliplr(p=1)
            >>> input_shape = (10, 10)
            >>> new = self._warp_imgaug(augmenter, input_shape)
            >>> new2 = self.warp(augmenter, input_shape)
        """
        new = self if inplace else self.__class__(self.data.copy(), self.meta)
        kpoi = new.to_imgaug(shape=input_shape)
        kpoi = augmenter.augment_keypoints(kpoi)
        xy = np.array([[kp.x, kp.y] for kp in kpoi.keypoints],
                      dtype=new.data['xy'].data.dtype)
        new.data['xy'].data = xy
        return new

    def warp(self, transform, input_shape=None, output_shape=None, inplace=False):
        """
        Generalized coordinate transform.

        Args:
            transform (GeometricTransform | ArrayLike | Augmenter):
                scikit-image tranform, a 3x3 transformation matrix, or
                an imgaug Augmenter.

            input_shape (Tuple): shape of the image these objects correspond to
                (only needed / used when transform is an imgaug augmenter)

            output_shape (Tuple): unused, only exists for compatibility

            inplace (bool, default=False): if True, modifies data inplace

        Example:
            >>> from kwimage.structs.points import *  # NOQA
            >>> self = Points.random(10, rng=0)
            >>> transform = skimage.transform.AffineTransform(scale=(2, 2))
            >>> new = self.warp(transform)
            >>> assert np.all(new.xy == self.scale(2).xy)

        Doctest:
            >>> self = Points.random(10, rng=0)
            >>> assert np.all(self.warp(np.eye(3)).xy == self.xy)
            >>> assert np.all(self.warp(np.eye(2)).xy == self.xy)
        """
        new = self if inplace else self.__class__(self.data.copy(), self.meta)
        if not isinstance(transform, (np.ndarray, skimage.transform._geometric.GeometricTransform)):
            import imgaug
            if isinstance(transform, imgaug.augmenters.Augmenter):
                return new._warp_imgaug(transform, input_shape, inplace=True)
            else:
                raise TypeError(type(transform))
        new.data['xy'] = new.data['xy'].warp(transform, input_shape,
                                             output_shape, inplace)
        return new

    def scale(self, factor, output_shape=None, inplace=False):
        """
        Scale a points by a factor

        Args:
            factor (float or Tuple[float, float]):
                scale factor as either a scalar or a (sf_x, sf_y) tuple.
            output_shape (Tuple): unused in non-raster spatial structures

        Example:
            >>> from kwimage.structs.points import *  # NOQA
            >>> self = Points.random(10, rng=0)
            >>> new = self.scale(10)
            >>> assert new.xy.max() <= 10
        """
        new = self if inplace else self.__class__(self.data.copy(), self.meta)
        new.data['xy'] = new.data['xy'].scale(factor, output_shape, inplace)
        return new

    def translate(self, offset, output_shape=None, inplace=False):
        """
        Shift the points up/down left/right

        Args:
            factor (float or Tuple[float]):
                transation amount as either a scalar or a (t_x, t_y) tuple.
            output_shape (Tuple): unused in non-raster spatial structures

        Example:
            >>> from kwimage.structs.points import *  # NOQA
            >>> self = Points.random(10, rng=0)
            >>> new = self.translate(10)
            >>> assert new.xy.min() >= 10
            >>> assert new.xy.max() <= 11
        """
        new = self if inplace else self.__class__(self.data.copy(), self.meta)
        new.data['xy'] = new.data['xy'].translate(offset, output_shape, inplace)
        return new


class Points(ub.NiceRepr, _PointsWarpMixin):
    """
    Stores multiple keypoints for a single object.

    This stores both the geometry and the class metadata if available

    Ignore:
        meta = {
         "names" = ['head', 'nose', 'tail'],
         "skeleton" = [(0, 1), (0, 2)],
        }

    Example:
        >>> from kwimage.structs.points import *  # NOQA
        >>> xy = np.random.rand(10, 2)
        >>> pts = Points(xy=xy)
        >>> print('pts = {!r}'.format(pts))
    """
    # __slots__ = ('data', 'meta',)

    # Pre-registered keys for the data dictionary
    __datakeys__ = ['xy', 'class_idxs']
    # Pre-registered keys for the meta dictionary
    __metakeys__ = ['classes']

    def __init__(self, data=None, meta=None, datakeys=None, metakeys=None,
                 **kwargs):
        if kwargs:
            if data or meta:
                raise ValueError('Cannot specify kwargs AND data/meta dicts')
            _datakeys = self.__datakeys__
            _metakeys = self.__metakeys__
            # Allow the user to specify custom data and meta keys
            if datakeys is not None:
                _datakeys = _datakeys + list(datakeys)
            if metakeys is not None:
                _metakeys = _metakeys + list(metakeys)
            # Perform input checks whenever kwargs is given
            data = {key: kwargs.pop(key) for key in _datakeys if key in kwargs}
            meta = {key: kwargs.pop(key) for key in _metakeys if key in kwargs}
            if kwargs:
                raise ValueError(
                    'Unknown kwargs: {}'.format(sorted(kwargs.keys())))

            if 'xy' in data:
                if isinstance(data['xy'], np.ndarray):
                    import kwimage
                    data['xy'] = kwimage.Coords(data['xy'])

        elif isinstance(data, self.__class__):
            # Avoid runtime checks and assume the user is doing the right thing
            # if data and meta are explicitly specified
            meta = data.meta
            data = data.data
        if meta is None:
            meta = {}
        self.data = data
        self.meta = meta

    def __nice__(self):
        data_repr = repr(self.xy)
        if '\n' in data_repr:
            data_repr = ub.indent('\n' + data_repr.lstrip('\n'), '    ')
        return 'xy={}'.format(data_repr)

    __repr__ = ub.NiceRepr.__str__

    def __len__(self):
        return len(self.data['xy'])

    @property
    def xy(self):
        return self.data['xy'].data

    def to_imgaug(self, shape):
        """
        Example:
            >>> from kwimage.structs.points import *  # NOQA
            >>> pts = Points.random(10)
            >>> shape = (10, 10)
            >>> kpoi = pts.to_imgaug(shape)
        """
        import imgaug
        kps = [imgaug.Keypoint(x, y) for x, y in self.data['xy'].data]
        kpoi = imgaug.KeypointsOnImage(kps, shape=shape)
        return kpoi

    @classmethod
    def from_imgaug(cls, kpoi):
        import numpy as np
        xy = np.array([[kp.x, kp.y] for kp in kpoi.keypoints])
        self = cls(xy=xy)
        return self

    @classmethod
    def random(Points, num=1, rng=None):
        """
        Makes random points; typically for testing purposes
        """
        rng = kwarray.ensure_rng(rng)
        self = Points(xy=rng.rand(num, 2))
        return self

    def is_numpy(self):
        return self.data['xy'].is_numpy()

    def is_tensor(self):
        return self.data['xy'].is_tensor()

    @_generic.memoize_property
    def _impl(self):
        return self.data['xy']._impl

    def tensor(self, device=ub.NoParam):
        impl = self._impl
        newdata = {k: impl.tensor(v, device) for k, v in self.data.items()}
        new = self.__class__(newdata, self.meta)
        return new

    def numpy(self):
        impl = self._impl
        newdata = {k: impl.numpy(v) for k, v in self.data.items()}
        new = self.__class__(newdata, self.meta)
        return new

    def draw_on(self, image):
        return self.xy.draw_on(image)

    def draw(self, color='blue', ax=None, alpha=None, radius=1):
        """
        Example:
            >>> from kwimage.structs.points import *  # NOQA
            >>> pts = Points.random(10)
            >>> pts.draw(radius=0.01)
        """
        import kwplot
        import matplotlib as mpl
        from matplotlib import pyplot as plt
        if ax is None:
            ax = plt.gca()
        xy = self.data['xy'].data

        # More grouped patches == more efficient runtime
        if alpha is None:
            alpha = [1.0] * len(xy)
        elif not ub.iterable(alpha):
            alpha = [alpha] * len(xy)

        ptcolors = [kwplot.Color(color, alpha=a).as01('rgba') for a in alpha]
        color_groups = ub.group_items(range(len(ptcolors)), ptcolors)

        default_centerkw = {
            'radius': radius,
            'fill': True
        }
        centerkw = default_centerkw.copy()
        for pcolor, idxs in color_groups.items():
            patches = [
                mpl.patches.Circle((x, y), ec=None, fc=pcolor, **centerkw)
                for x, y in xy[idxs]
            ]
            col = mpl.collections.PatchCollection(patches, match_original=True)
            ax.add_collection(col)


class PointsList(_generic.ObjectList):
    """
    Stores a list of Points, each item usually corresponds to a different object.

    Notes:
        # TODO: when the data is homogenous we can use a more efficient
        # representation, otherwise we have to use heterogenous storage.
    """
