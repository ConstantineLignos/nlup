# Copyright (C) 2014 Kyle Gorman
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
# OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# PLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

"""
perceptron: perceptron-like classifers, including:

* `BinaryPerceptron`: binary perceptron classifier
* `Perceptron`: multiclass perceptron classifier
* `SequencePerceptron`: multiclass perceptron for sequence tagging
* `BinaryAveragedPerceptron`: binary averaged perceptron classifier
* `AveragedPerceptron`: multiclass averaged perceptron
* `SequenceAveragedPerceptron`: multiclass averaged perceptron for
   sequence tagging
"""


import logging

from time import time
from random import Random
from functools import partial
from operator import itemgetter
from collections import defaultdict, namedtuple

from .confusion import Accuracy
from .jsonable import JSONable
from .decorators import listify, reversify


INF = float("inf")
ORDER = 0
EPOCHS = 1


class Classifier(JSONable):

    """
    Mixin for shared classifier methods
    """

    def fit(self, X, Y, epochs=EPOCHS):
        data = list(zip(X, Y))  # which is a copy
        logging.info("Starting {} epoch(s) of training.".format(epochs))
        for epoch in range(1, 1 + epochs):
            logging.info("Starting epoch {:>2}.".format(epoch))
            tic = time()
            accuracy = Accuracy()
            self.random.shuffle(data)
            for (x, y) in data:
                yhat = self.fit_one(x, y)
                accuracy.update(y, yhat)
            logging.debug("Epoch {:>2} accuracy: {}".format(epoch,
                                       self._accuracy_str(accuracy)))
            logging.debug("Epoch {:>2} time elapsed: {}.".format(epoch,
                                       self._time_elapsed_str(tic)))
        self.finalize()

    def _accuracy_str(self, accuracy):
        return "{:.04f}".format(accuracy.accuracy)

    def _time_elapsed_str(self, tic):
        return "{}s".format(int(time() - tic))


class BinaryPerceptron(Classifier):

    """
    Binary perceptron classifier
    """

    def __init__(self, *, seed=None):
        self.random = Random(seed)
        self.weights = defaultdict(int)

    def score(self, x):
        """
        Get score for a `hit` according to the feature vector `x`
        """
        return sum(self.weights[feature] for feature in x)

    def predict(self, x):
        """
        Predict binary decision for the feature vector `x`
        """
        return self.score(x) >= 0

    def fit_one(self, x, y):
        yhat = self.predict(x)
        if y != yhat:
            self.update(x, y)
        return yhat

    def update(self, x, y, tau=1):
        """
        Given feature vector `x`, reward correct observation `y` with
        the update `tau`
        """
        if y is False:
            tau *= -1
        for feature in x:
            self.weights[feature] += tau

    def finalize(self):
        """
        Prepare for inference by removing zero-valued weights 
        """
        self.weights = {feature: weight for feature in weight if 
                        weight != 0}

class Perceptron(Classifier):

    """
    The multiclass perceptron with sparse binary feature vectors:

    Each class (i.e., label, outcome) is represented as a hashable item,
    such as a string. Features are represented as hashable objects
    (preferably strings, as Python dictionaries have been aggressively
    optimized for this case). Presence of a feature indicates that that
    feature is "firing" and absence indicates that that it is not firing.
    This class is primarily to be used as an abstract base class; in most
    cases, the regularization and stability afforded by the averaged
    perceptron (`AveragedPerceptron`) will be worth it.

    The perceptron was first proposed in the following paper:

    F. Rosenblatt. 1958. The perceptron: A probabilistic model for
    information storage and organization in the brain. Psychological
    Review 65(6): 386-408.
    """

    # constructor

    def __init__(self, *, default=None, seed=None):
        self.classes = {default}
        self.random = Random(seed)
        self.weights = defaultdict(partial(defaultdict, int))

    def score(self, x, y):
        """
        Get score for one class (`y`) according to the feature vector `x`
        """
        return sum(self.weights[feature][y] for feature in x)

    def scores(self, x):
        """
        Get scores for all classes according to the feature vector `x`
        """
        scores = dict.fromkeys(self.classes, 0)
        for feature in x:
            for (cls, weight) in self.weights[feature].items():
                scores[cls] += weight
        return scores

    def predict(self, x):
        """
        Predict most likely class for the feature vector `x`
        """
        scores = self.scores(x)
        (argmax_score, _) = max(scores.items(), key=itemgetter(1))
        return argmax_score

    def fit_one(self, x, y):
        self.classes.add(y)
        yhat = self.predict(x)
        if y != yhat:
            self.update(x, y, yhat)
        return yhat

    def update(self, x, y, yhat, tau=1):
        """
        Given feature vector `x`, reward correct observation `y` and
        punish incorrect hypothesis `yhat` with the update `tau`
        """
        for feature in x:
            feature_ptr = self.weights[feature]
            feature_ptr[y] += tau
            feature_ptr[yhat] -= tau

    def finalize(self):
        """
        Prepare for inference by removing zero-valued weights 
        """
        self.weights = {feature: {cls: weight for
                                 (cls, weight) in clsweight.items() if
                                       weight != 0} for
                       (feature, clsweight) in self.weights.items()}

TrellisCell = namedtuple("TrellisCell", ["score", "pointer"])


class SequencePerceptron(Perceptron):

    """
    Perceptron with Viterbi-decoding powers
    """

    def __init__(self, *, tfeats_fnc, order=ORDER, **kwargs):
        super(SequencePerceptron, self).__init__(**kwargs)
        self.tfeats_fnc = tfeats_fnc
        self.order = order

    def predict(self, xx):
        """
        Tag a sequence using a greedy approximation of the Viterbi 
        algorithm, in which each sequence is tagged using transition
        features based on earlier hypotheses. The time complexity of this 
        operation is O(nt) where n is sequence length and t is the 
        cardinality of the tagset. 

        Alternatively a sequence can be tagged using the Viterbi algorithm:

        1. Compute tag-given-token forward probabilities and backtraces
        2. Compute the most probable final state
        3. Follow backtraces from this most probable state to generate
           the most probable tag sequence.

        The time complexity of this operation is O(n t^2) where n is the
        sequence length and t is the cardinality of the tagset.
        """
        if self.order <= 0:
            return self._markov0_predict(xx)
        else:        
            (_, yyhat) = self._greedy_predict(xx)
        return yyhat
        # FIXME(kbg) disabled Viterbi decoding for the moment
        """
        if not xx:
            return []
        trellis = self._trellis(xx)
        (best_last_state, _) = max(trellis[-1].items(), key=itemgetter(1))
        return self._traceback(trellis, best_last_state)
        """

    def predict_with_transitions(self, xx):
        """
        Same as above, but hacked to give you the xx's back
        """
        if self.order <=  0:
            return (xx, self._markov0_predict(xx))
        else:
            return self._greedy_predict_with_transitions(xx)

    @listify
    def _markov0_predict(self, xx):
        """
        Sequence classification with a Markov order-0 model
        """
        for x in xx:
            (yhat, _) = max(self.scores(x).items(), key=itemgetter(1))
            yield yhat

    def _greedy_predict(self, xx):
        """
        Sequence classification with a greedy approximation of a Markov
        model, also returning `xx` augmented with the appropriate
        transition features
        """
        xxt = []
        yyhat = []
        for x in xx:
            xt = x + self.tfeats_fnc(sequence[-self.order:])
            xxt.append(xt)
            (yhat, _) = max(self.scores(xt).items(), key=itemgetter(1))
            yyhat.append(yhat)
        return (xxt, yyhat)

    def _trellis(self, xx):
        """
        Construct the trellis for Viterbi decoding assuming a non-zero
        Markov order. The trellis is represented as a list, in which each 
        element represents a single point in time. These elements are 
        dictionaries mapping from state labels to `TrellisCell` elements, 
        which contain the state score and a backpointer.
        """
        # first case is special
        trellis = [{state: TrellisCell(score, None) for (state, score) in
                    self.scores(xx[0]).items()}]
        for x in xx[1:]:
            pcolumns = trellis[-self.order:]
            # store previous state scores
            pscores = {state: score for (state, (score, pointer)) in
                       pcolumns[-1].items()}
            # store best previous state + transmission scores
            ptscores = {state: TrellisCell(-INF, None) for state in
                        self.classes}
            # find the previous state which maximizes the previous state +
            # the transmission scores
            for (pstate, pscore) in pscores.items():
                tfeats = self.tfeats_fnc(self._traceback(pcolumns, pstate))
                for (state, tscore) in self.scores(tfeats).items():
                    ptscore = pscore + tscore
                    (best_ptscore, _) = ptscores[state]
                    if ptscore > best_ptscore:
                        ptscores[state] = TrellisCell(ptscore, pstate)
            # combine emission, previous state, and transmission scores
            column = {}
            for (state, escore) in self.scores(x).items():
                (ptscore, pstate) = ptscores[state]
                column[state] = TrellisCell(ptscore + escore, pstate)
            trellis.append(column)
        return trellis

    @reversify
    def _traceback(self, trellis, state):
        for column in reversed(trellis):
            yield state
            state = column[state].pointer

    def fit_one(self, xx, yy):
        self.classes.update(yy)
        # decode to get predicted sequence
        (xxt, yyhat) = self.predict_with_transitions(xx)
        for (i, (xt, y, yhat)) in enumerate(zip(xxt, yy, yyhat)):
            if y != yhat:
                print(xt)
                self.update(xt, y, yhat)
        return yyhat

    def fit(self, XX, YY, epochs=EPOCHS):
        data = list(zip(XX, YY))
        logging.info("Starting {} epoch(s) of training.".format(epochs))
        for epoch in range(1, 1 + epochs):
            logging.info("Starting epoch {:>2}.".format(epoch))
            tic = time()
            accuracy = Accuracy()
            self.random.shuffle(data)
            for (xx, yy) in data:
                yyhat = self.fit_one(xx, yy)
                for (y, yhat) in zip(yy, yyhat):
                    accuracy.update(y, yhat)
            logging.debug("Epoch {:>2} accuracy: {}".format(epoch,
                                       self._accuracy_str(accuracy)))
            logging.debug("Epoch {:>2} time elapsed: {}.".format(epoch,
                                       self._time_elapsed_str(tic)))
        self.finalize()


class LazyWeight(object):

    """
    Helper class for `AveragedPerceptron`:

    Instances of this class are essentially triplets of values which
    represent a weight of a single feature in an averaged perceptron.
    This representation permits "averaging" to be done implicitly, and
    allows us to take advantage of sparsity in the feature space.
    First, as the name suggests, the `summed_weight` variable is lazily
    evaluated (i.e., computed only when needed). This summed weight is the
    one used in actual inference: we need not average explicitly. Lazy
    evaluation requires us to store two other numbers. First, we store the
    current weight, and the last time this weight was updated. When we
    need the real value of the summed weight (for inference), we "freshen"
    the summed weight by adding to it the product of the real weight and
    the time elapsed.

    # initialize
    >>> t = 0
    >>> lw = LazyWeight(t=t)
    >>> t += 1
    >>> lw.update(t, 1)
    >>> t += 1
    >>> lw.get()
    1

    # some time passes...
    >>> t += 1
    >>> lw.get()
    1

    # weight is now changed
    >>> lw.update(-1, t)
    >>> t += 3
    >>> lw.update(-1, t)
    >>> t += 3
    >>> lw.get()
    -1
    """

    def __init__(self, default_factory=int, t=0):
        self.timestamp = t
        self.weight = default_factory()
        self.summed_weight = default_factory()

    def __repr__(self):
        return '{}({})'.format(self.__class__.__name__, self.__dict__)

    def get(self):
        """
        Return current weight
        """
        return self.weight

    def _freshen(self, t):
        """
        Apply queued updates, and update the timestamp
        """
        self.summed_weight += (t - self.timestamp) * self.weight
        self.timestamp = t

    def update(self, value, t):
        """
        Bring sum of weights up to date, then add `value` to the weight
        """
        self._freshen(t)
        self.weight += value

    def average(self, t):
        """
        Set `self.weight` to the summed value, for final inference
        """
        self._freshen(t)
        self.weight = self.summed_weight / t

class BinaryAveragedPerceptron(BinaryPerceptron):

    def __init__(self, *, seed=None):
        self.random = Random(seed)
        self.weights = defaultdict(LazyWeight)
        self.time = 0

    def predict(self, x):
        """
        Predict most likely class for the feature vector `x`
        """
        score = sum(self.weights[feature].get() for feature in x)
        return score >= 0

    def fit_one(self, x, y):
        retval = super(BinaryAveragedPerceptron, self).fit_one(x, y)
        self.time += 1
        return retval

    def update(self, x, y, tau=1):
        """
        Given feature vector `x`, reward correct observation `y` and
        punish incorrect hypothesis `yhat` with the update `tau`, 
        assuming that `y != yhat`.
        """
        if y is False:
            tau *= -1
        elif y is not True:
            raise ValueError("y is not boolean")
        for feature in x:
            self.weights[feature].update(tau, self.time)

    def finalize(self):
        """
        Prepare for inference by removing zero-valued weights and applying
        averaging
        """
        ready2die = []
        for (feature, weight) in self.weights.items():
            if weight == 0.:
                ready2die.append(feature)
            else:
                weight.average(self.time)
        for feature in ready2die:
            del self.weights[feature]


class AveragedPerceptron(Perceptron):

    """
    The multiclass perceptron with sparse binary feature vectors, with
    averaging for stability and regularization.

    Averaging was originally proposed in the following paper:

    Y. Freund and R.E. Schapire. 1999. Large margin classification using
    the perceptron algorithm. Machine Learning 37(3): 227-296.
    """

    def __init__(self, *, default=None, seed=None):
        self.classes = {default}
        self.random = Random(seed)
        self.weights = defaultdict(partial(defaultdict, LazyWeight))
        self.time = 0

    def score(self, x, y):
        """
        Get score for one class (`y`) according to the feature vector `x`
        """
        return sum(self.weights[feature][y].get() for feature in x)

    def scores(self, x):
        """
        Get scores for all classes according to the feature vector `x`
        """
        scores = dict.fromkeys(self.classes, 0)
        for feature in x:
            for (cls, weight) in self.weights[feature].items():
                scores[cls] += weight.get()
        return scores

    def fit_one(self, x, y):
        retval = super(AveragedPerceptron, self).fit_one(x, y)
        self.time += 1
        return retval

    def update(self, x, y, yhat, tau=1):
        """
        Given feature vector `x`, reward correct observation `y` and
        punish incorrect hypothesis `yhat` with the update `tau`
        """
        for feature in x:
            feature_ptr = self.weights[feature]
            feature_ptr[y].update(+tau, self.time)
            feature_ptr[yhat].update(-tau, self.time)

    def finalize(self):
        """
        Prepare for inference by removing zero-valued weights and applying
        averaging
        """
        ready2die = []
        for (feature, clsweights) in self.weights.items():
            for (cls, weight) in clsweights.items():
                if weight == 0.:
                    ready2die.append((feature, cls))
                else:
                    weight.average(self.time)
        for (feature, cls) in ready2die:    
            del self.weights[feature][cls]


class SequenceAveragedPerceptron(AveragedPerceptron, SequencePerceptron):

    def __init__(self, *, tfeats_fnc=None, order=ORDER, **kwargs):
        super(SequenceAveragedPerceptron, self).__init__(**kwargs)
        self.tfeats_fnc = tfeats_fnc
        self.order = order
