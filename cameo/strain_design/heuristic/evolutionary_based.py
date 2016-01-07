# Copyright 2015 Novo Nordisk Foundation Center for Biosustainability, DTU.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

# http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from __future__ import absolute_import, print_function

import inspyred
from cameo.strain_design.heuristic.evolutionary.objective_functions import biomass_product_coupled_min_yield, \
    biomass_product_coupled_yield
from pandas import DataFrame

from cameo import fba
from cameo.core import SolverBasedModel
from cameo.strain_design.heuristic.evolutionary.optimization import GeneKnockoutOptimization, \
    ReactionKnockoutOptimization
from cameo.strain_design.heuristic.evolutionary.processing import process_knockout_solution
from cameo.strain_design.strain_design import StrainDesignMethod, StrainDesignResult, StrainDesign
from cameo.util import ProblemCache
from cameo import ui


class OptGene(StrainDesignMethod):
    def __init__(self, model, evolutionary_algorithm=inspyred.ec.GA, manipulation_type="genes", essential_genes=None,
                 essential_reactions=None, *args, **kwargs):
        assert isinstance(model, SolverBasedModel)

        super(OptGene, self).__init__(*args, **kwargs)

        self._model = model
        self._algorithm = evolutionary_algorithm
        self._optimization_algorithm = None
        self._manipulation_type = None
        self._essential_genes = essential_genes
        self._essential_reactions = essential_reactions
        self.manipulation_type = manipulation_type

    @property
    def manipulation_type(self):
        return self._manipulation_type

    @manipulation_type.setter
    def manipulation_type(self, manipulation_type):
        self._manipulation_type = manipulation_type
        if manipulation_type is "genes":
            self._optimization_algorithm = GeneKnockoutOptimization(
                model=self._model,
                heuristic_method=self._algorithm,
                essential_genes=self._essential_genes)
        elif manipulation_type is "reactions":
            self._optimization_algorithm = ReactionKnockoutOptimization(
                model=self._model,
                heuristic_method=self._algorithm,
                essential_reactions=self._essential_reactions
            )
        else:
            raise ValueError("Invalid manipulation type %s" % manipulation_type)

    def run(self, target=None, biomass=None, substrate=None, knockouts=5, simulation_method=fba, robust=True,
            max_evaluations=20000, population_size=100, time_machine=None, **kwargs):
        """
        Parameters
        ----------
        target: str, Metabolite or Reaction
            The design target
        biomass: str, Metabolite or Reaction
            The biomass definition in the model
        substrate: str, Metabolite or Reaction
            The main carbon source
        knockouts: int
            Max number of knockouts allowed
        simulation_method: function
            Any method from cameo.flux_analysis.simulation or equivalent
        robust: bool
            If true will use the minimum flux rate to compute the fitness
        max_evaluations: int
            Number of evaluations before stop
        population_size: int
            Number of individuals in each generation
        time_machine: TimeMachine
            See TimeMachine
        kwargs: dict
            Arguments for the simulation method.

        Returns
        -------

        OptGeneResult
        """

        target = self._model.reaction_for(target, time_machine=time_machine)
        biomass = self._model.reaction_for(biomass, time_machine=time_machine)
        substrate = self._model.reaction_for(substrate, time_machine=time_machine)

        if robust:
            objective_function = biomass_product_coupled_min_yield(biomass, target, substrate)
        else:
            objective_function = biomass_product_coupled_yield(biomass, target, substrate)

        self._optimization_algorithm.objective_function = objective_function
        self._optimization_algorithm.simulation_kwargs = kwargs
        self._optimization_algorithm.simulation_method = simulation_method

        result = self._optimization_algorithm.run(max_evaluations=max_evaluations,
                                                  popuplation_size=population_size,
                                                  product=target,
                                                  biomass=biomass,
                                                  max_size=knockouts,
                                                  maximize=True,
                                                  **kwargs)

        return OptGeneResult(self._model, result, objective_function, simulation_method, self._manipulation_type,
                             biomass, target, substrate, kwargs)


class OptGeneResult(StrainDesignResult):
    __method_name__ = "OptGene"

    __aggregation_function = {
        "genes": lambda x: tuple(tuple(e for e in elements) for elements in x.values)
    }

    def __init__(self, model, solutions, objective_function, simulation_method, manipulation_type,
                 biomass, target, substrate, simulation_kwargs, *args, **kwargs):
        super(OptGeneResult, self).__init__(*args, **kwargs)
        assert isinstance(model, SolverBasedModel)

        self._model = model
        self._solutions = solutions
        self._objective_function = objective_function
        self._simulation_method = simulation_method
        self._manipulation_type = manipulation_type
        self._biomass = biomass
        self._target = target
        self._substrate = substrate
        self._processed_solutions = None
        self._simulation_kwargs = simulation_kwargs

    def __iter__(self):
        for solution in self._solutions:
            yield StrainDesign(knockouts=solution, manipulation_type=self._manipulation_type)

    def __len__(self):
        if self._processed_solutions is None:
            self._process_solutions()
        return len(self._processed_solutions)

    def plot(self, grid=None, width=None, height=None, title=None):
        pass

    @property
    def data_frame(self):
        if self._processed_solutions is None:
            self._process_solutions()

        if self._manipulation_type == "reactions":
            data_frame =  DataFrame(self._processed_solutions)
        else:
            columns = self._processed_solutions.columns.difference(["reactions", "size"])
            aggregation_functions = {k: self.__aggregation_function.get(k, lambda x: x.values[0]) for k in columns}
            data_frame = self._processed_solutions.groupby(["reactions", "size"], as_index=False)\
                .aggregate(aggregation_functions)
            data_frame = data_frame[self._processed_solutions.columns]

        data_frame.sort_values("size", inplace=True)
        return data_frame

    def _process_solutions(self):
        ui.notice("Processing solutions")
        loader = ui.loading()
        processed_solutions = DataFrame(columns=["reactions", "genes", "size", "fva_min", "fva_max", "fbid",
                                                 "target_flux", "biomass_flux", "yield", "fitness"])

        cache = ProblemCache(self._model)
        for i, solution in enumerate(self._solutions):
            processed_solutions.loc[i] = process_knockout_solution(
                self._model, solution, self._simulation_method, self._simulation_kwargs, self._biomass,
                self._target, self._substrate, [self._objective_function], cache=cache)

        if self._manipulation_type == "reactions":
            processed_solutions.drop('genes', axis=1, inplace=True)
        ui.stop_loader(loader)
        self._processed_solutions = processed_solutions
